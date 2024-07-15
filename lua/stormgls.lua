local lspconfig = require 'lspconfig'
local configs = require 'lspconfig.configs'
local util = require 'lspconfig.util'

local M = {}

configs.storm = {
  default_config = {
    cmd = { "python", "../stormgls/stormgls.py" },
    filetypes = {'storm'},
    autostart = true,
    single_file_support = true,
    root_dir = util.find_git_ancestor,
    settings = {},
  },
}

lspconfig.storm.setup{}

return M
