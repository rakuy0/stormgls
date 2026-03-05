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

- These startup time costs are why autocomplete can be rather finnicky. You have to wait until you get the "storm ready" at the end of the lsinit function to have the completions work properly.
- Maybe a configuration option to connect to a cortex to pull various commands, extended model elements, etc?
- Maybe more robust symbol detection? That way if the file is invalid on start, we can still get something.
- semantic highlighting for parameter names and whatnot
- Function call semantics (we could detect things like function calls not matching the number of parameters, maybe show what kind of a function something is?)
- renaming would be cool.
- codeLens for some contextual information
    - we have all the doc info for things like API parameters that we could add in?
    - Could that be useful for creating a rudimentary type system for storm?
- variable names in edge autocompletion
- DIAGNOSTIC - Usage of undeclared variable
- DIAGNOSTIC - return outside of function warning?
- Configurable warnings/errors
- Deprecated library parameters?
- Autoformatter (AST -> code printer?)
- better local variable autocomplete (and unused variables/sets)?
- detect that a function doesn't do anything (no return/node lift/etc)?
- pycharm and vscode integrations?
- synapse 3.x compat?
- local variable autocomplete not working in if statement conditionals?
- One visitor pattern to rule them all? Would help with finding undeclared variables
- wrap a bunch of the lsprotocol.types calls so we don't have to make things a bit more readable

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
