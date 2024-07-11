To activate inlay hints (which aren't supported yet via this LS), but maybe for later:
- vim.lsp.inlay_hint.enable(not vim.lsp.inlay_hint.is_enabled())

You're going to want some autocomplete plugin like nvim-cmp
- Otherwise you can use something like Ctrl+X and Ctrl+O for neovim's omnifunc autocomplete, which is...not great

TODO:
- Cache things like the model and autocomplete so the startup times aren't awful.
  - These startup time costs are why autocomplete can be rather finnicky. You have to wait
    until you get the "storm ready" at the end of the lsinit function to have it work properly.
- Actual plugin installation via something like lazy.nvim
- Maybe a configuration option to connect to a cortex to pull various commands, extended model elements, etc?


Until I get around to the plugin installation, I'm doing local dev on this via some lua config::

    local lspconfig = require 'lspconfig'
    local configs = require 'lspconfig.configs'
    local util = require 'lspconfig.util'

    configs.storm = {
      default_config = {
        cmd = {"python", "/home/rakuyo/code/stormgls/stormgls/stormgls.py"},
        filetypes = {'storm'},
        autostart = true,
        single_file_support = true,
        root_dir = util.find_git_ancestor,
        settings = {},
      },
    }

    lspconfig.storm.setup{}
    vim.cmd("autocmd BufRead,BufNewFile *.storm set filetype=storm")

    vim.lsp.inlay_hint.enable(not vim.lsp.inlay_hint.is_enabled())
