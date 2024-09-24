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
- Combine this with vim-storm
- semantic highlighting for parameter names and whatnot
- Function call semantics (we could detect things like function calls not matching the number of parameters, maybe show what kind of a function something is?)
- cache function signatures?
- renaming would be cool
- codeLens for some contextual information.
    - we have all the doc info for things like API parameters that we could add in?
        - but generally more SA around API parameters would be neat.
    - Could that be useful for creating a rudimentary type system for storm?
- edges in completions (when we're in light edge syntax)
- DIAGNOSTIC - Usage of undeclared variable
- pygls 2.0 is about to drop, see migration guide: https://pygls.readthedocs.io/en/latest/howto/migrate-to-v2.html

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
