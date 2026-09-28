import assert from "node:assert/strict";
import { test } from "node:test";
import guard from "../.pi/extensions/bonsai-guard.ts";

const handlers = {};
guard({ on: (name, fn) => { (handlers[name] ??= []).push(fn); } });
const fire = (name, event, ctx) => handlers[name][0](event, ctx);

const makeCtx = (id = "ternary-bonsai-2-27b") => {
	const ctx = { model: { id }, hasUI: false, aborts: 0 };
	ctx.abort = () => { ctx.aborts += 1; };
	return ctx;
};
const assistant = (content, stopReason = "stop") => ({ role: "assistant", content, stopReason });
const thinking = { type: "thinking", thinking: "reasoning", thinkingSignature: "reasoning_content" };
const call = { type: "toolCall", id: "c1", name: "bash", arguments: { command: "python3 scripts/log.py Банан" } };

test("request to bonsai gets temperature 0 and template kwargs, others are untouched", () => {
	const out = fire("before_provider_request",
		{ payload: { model: "ternary-bonsai-2-27b", messages: [], chat_template_kwargs: { enable_thinking: true } } }, makeCtx());
	assert.equal(out.temperature, 0);
	assert.deepEqual(out.chat_template_kwargs, { enable_thinking: true, reasoning_effort: "xhigh", preserve_thinking: false });
	assert.equal(fire("before_provider_request", { payload: { model: "x" } }, makeCtx("qwen3.5-0.8b")), undefined);
});

test("streaming aborts once, at the first leaked tag", () => {
	const ctx = makeCtx();
	fire("message_start", { message: assistant([]) }, ctx);
	fire("message_update", { message: assistant([thinking, { type: "text", text: "Записал." }]) }, ctx);
	assert.equal(ctx.aborts, 0);
	fire("message_update", { message: assistant([thinking, { type: "text", text: "Записал.\n</think>\n" }]) }, ctx);
	fire("message_update", { message: assistant([thinking, { type: "text", text: "Записал.\n</think>\nГотово." }]) }, ctx);
	assert.equal(ctx.aborts, 1);
	fire("message_start", { message: assistant([]) }, ctx);
	fire("message_update", { message: assistant([{ type: "text", text: "a</think>b" }]) }, ctx);
	assert.equal(ctx.aborts, 2);
});

test("live answer: text cut at the tag, tool calls after it dropped, stop reason normal", () => {
	const msg = { ...assistant([thinking, { type: "text", text: "Записал.\n</think>\n\nЕщё раз: записал." }, call], "aborted"),
		errorMessage: "Operation aborted" };
	const out = fire("message_end", { message: msg }, makeCtx()).message;
	assert.deepEqual(out.content, [thinking, { type: "text", text: "Записал." }]);
	assert.equal(out.stopReason, "stop");
	assert.equal("errorMessage" in out, false);
});

test("live answer that opens with the tag keeps the first real segment", () => {
	const out = fire("message_end", { message: assistant([{ type: "text", text: "</think>\n\nОтвет\n</think>хвост" }]) }, makeCtx()).message;
	assert.deepEqual(out.content, [{ type: "text", text: "Ответ" }]);
});

test("clean answers and other models pass through", () => {
	assert.equal(fire("message_end", { message: assistant([thinking, { type: "text", text: "Ок" }, call], "toolUse") }, makeCtx()), undefined);
	assert.equal(fire("message_end", { message: assistant([{ type: "text", text: "a</think>b" }]) }, makeCtx("other")), undefined);
	assert.equal(fire("message_end", { message: { role: "user", content: "a</think>b" } }, makeCtx()), undefined);
});

test("history: recorded answers lose the tail text but keep their tool calls", () => {
	const leaked = assistant([thinking, { type: "text", text: "Итог.\n</think>\nИтог ещё раз." }, call], "toolUse");
	const result = { role: "toolResult", toolCallId: "c1", toolName: "bash", content: [{ type: "text", text: "ok" }] };
	const user = { role: "user", content: [{ type: "text", text: "дальше" }] };
	const out = fire("context", { messages: [user, leaked, result] }, makeCtx()).messages;
	assert.deepEqual(out[1].content, [thinking, { type: "text", text: "Итог." }, call]);
	assert.equal(out[1].stopReason, "toolUse");
	assert.strictEqual(out[0], user);
	assert.strictEqual(out[2], result);
	assert.equal(fire("context", { messages: [user, result] }, makeCtx()), undefined);
});
