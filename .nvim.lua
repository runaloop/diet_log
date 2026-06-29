vim.schedule(function()
  local h = vim.o.lines
  vim.cmd("edit today.md")
  vim.cmd("belowright split ration.md")
  vim.cmd("belowright split pills.md")
  vim.cmd("wincmd t")
  vim.cmd("resize " .. math.floor(h * 0.65))
  vim.cmd("wincmd j")
  vim.cmd("resize " .. math.floor(h * 0.28))
  vim.cmd("normal! gg")
  vim.cmd("wincmd t")
end)

vim.api.nvim_create_autocmd({ "BufEnter", "BufWinEnter" }, {
  pattern = "*.md",
  callback = function()
    vim.schedule(function()
      vim.opt_local.spell = false
    end)
  end,
})
