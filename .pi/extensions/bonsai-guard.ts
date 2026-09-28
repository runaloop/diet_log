// Project-only settings and a stop guard for the local Ternary-Bonsai model.
//
// The model sometimes answers, writes a literal </think> and keeps generating: it
// repeats the answer, invents the user's reply and calls tools on it. The server
// consumes only the first </think> (end of reasoning), so any </think> in the
// visible text marks everything after it as that runaway continuation.
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

const MODEL_ID = "ternary-bonsai-2-27b";
const TAG = "</think>";
// The chat template accepts only low, medium and xhigh.
const REASONING_EFFORT = "xhigh";

type Part = { type: string; text?: string };
type Msg = { role: string; content?: unknown; stopReason?: string; errorMessage?: string };

const isBonsai = (ctx: ExtensionContext) => ctx.model?.id === MODEL_ID;

function leakIndex(msg: Msg): number {
	if (msg.role !== "assistant" || !Array.isArray(msg.content)) return -1;
	return (msg.content as Part[]).findIndex((p) => p.type === "text" && p.text?.includes(TAG));
}

const firstSegment = (text: string) => (text.split(TAG).find((s) => s.trim()) ?? "").trim();

// A live answer: everything after the leak, tool calls included, has not run yet.
function cutLive<M extends Msg>(msg: M): M | undefined {
	const i = leakIndex(msg);
	if (i < 0) return undefined;
	const parts = msg.content as Part[];
	const kept = firstSegment(parts[i].text!);
	const content = [...parts.slice(0, i), ...(kept ? [{ ...parts[i], text: kept }] : [])];
	const { errorMessage: _dropped, ...rest } = msg;
	return { ...rest, content, stopReason: "stop" } as M;
}

// A recorded answer: its tool calls already ran and have results after it, so only
// the text is trimmed.
function cutRecorded<M extends Msg>(msg: M): M | undefined {
	if (leakIndex(msg) < 0) return undefined;
	const content = (msg.content as Part[]).flatMap((p) => {
		if (p.type !== "text" || !p.text?.includes(TAG)) return [p];
		const kept = firstSegment(p.text);
		return kept ? [{ ...p, text: kept }] : [];
	});
	return { ...msg, content } as M;
}

export default function (pi: ExtensionAPI) {
	let aborted = false;

	pi.on("before_provider_request", (event, ctx) => {
		if (!isBonsai(ctx)) return;
		const payload = event.payload as Record<string, any>;
		return {
			...payload,
			temperature: 0,
			chat_template_kwargs: {
				...payload.chat_template_kwargs,
				reasoning_effort: REASONING_EFFORT,
				preserve_thinking: false,
			},
		};
	});

	pi.on("message_start", () => {
		aborted = false;
	});

	pi.on("message_update", (event, ctx) => {
		if (aborted || !isBonsai(ctx) || leakIndex(event.message as Msg) < 0) return;
		aborted = true;
		ctx.abort();
	});

	pi.on("message_end", (event, ctx) => {
		if (!isBonsai(ctx)) return;
		const cleaned = cutLive(event.message as Msg);
		if (!cleaned) return;
		if (ctx.hasUI) ctx.ui.notify(`bonsai: ответ обрезан на ${TAG}, всё после него отброшено`, "warning");
		return { message: cleaned as typeof event.message };
	});

	// Sessions recorded before this guard still carry leaked tails; left in the
	// history they teach the model the same pattern.
	pi.on("context", (event, ctx) => {
		if (!isBonsai(ctx)) return;
		let changed = false;
		const messages = event.messages.map((m) => {
			const cleaned = cutRecorded(m as Msg);
			if (!cleaned) return m;
			changed = true;
			return cleaned as typeof m;
		});
		return changed ? { messages } : undefined;
	});
}
