local lspconfig = require 'lspconfig'
local configs = require 'lspconfig.configs'
local util = require 'lspconfig.util'

local dir = vim.fn.fnamemodify(debug.getinfo(1, "S").source:sub(2), ":p:h")

local M = {}

configs.storm = {
  default_config = {
    cmd = { "python", dir .. "/../../stormgls/stormgls.py" },
    filetypes = {'storm'},
    autostart = true,
    single_file_support = true,
    root_dir = util.find_git_ancestor,
    settings = {
      datadir = vim.fn.stdpath('data')
    },
  },
}

lspconfig.storm.setup{}

vim.api.nvim_create_user_command('StormUpdate', function()
    local job = vim.fn.jobstart('pip install -r ' .. dir .. '/../../requirements.txt')
    --, { cwd = '/path/to/working/dir', on_exit = some_function, on_stdout = some_other_function, on_stderr = some_third_function } ) 
end, {})

return M
