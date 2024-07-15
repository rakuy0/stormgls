stormgls
=======

A language server implementation for storm via pygls.


WARNING
-------

This is very much work in progress. Use at your own risk.

Notes
-----

To activate inlay hints (which aren't supported yet via this LS), but maybe for later:

- vim.lsp.inlay_hint.enable(not vim.lsp.inlay_hint.is_enabled())

You're going to want some autocomplete plugin like nvim-cmp:

- Otherwise you can use something like Ctrl+X and Ctrl+O for neovim's omnifunc autocomplete, which is...not great. It doesn't quite work how you want it to, so I'd avoid it.

To Do List
----------

- Cache things like the model and autocomplete so the startup times aren't awful.
  - These startup time costs are why autocomplete can be rather finnicky. You have to wait until you get the "storm ready" at the end of the lsinit function to have the completions work properly.
- Maybe a configuration option to connect to a cortex to pull various commands, extended model elements, etc?
- Maybe more robust symbol detection? That way if the file is invalid on start, we can still get something.
- Combine this with vim-storm (and update for new keywords?)
- Make plugin installation a bit smoother and auto-install python deps if missing


Installation
------------

If you wanna do some dev on this, clone this repo and run::

    pip install -r requirements.txt

And add something like this to your neovim config::

    return {
        '/path/to/my/stormgls/',
        version = '*'
    }
    
If you're just using this, you should just be able to do something like this in your neovim instance::

    return {
        'rakuy0/stormgls',
        version = '*'
        build = ':StormUpdate',
    }
