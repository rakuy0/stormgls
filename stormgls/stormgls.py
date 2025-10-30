import re
import sys
import enum
import asyncio
import pathlib
import tempfile
import contextlib

import synapse.exc as s_exc

import synapse.lib.ast as s_ast
import synapse.lib.parser as s_parser
import synapse.lib.autodoc as s_autodoc
import synapse.lib.msgpack as s_msgpack
import synapse.lib.version as s_version
import synapse.lib.stormtypes as s_stormtypes

from pygls.server import LanguageServer
from pygls.workspace import TextDocument

from lsprotocol import types

WORD = re.compile(r'\$?[\w\:\.]+')

JSONEXPR = ('lib.null', 'lib.false', 'lib.true')
TokenTypes = ["keyword", "variable", "function", "operator", "parameter", "type", "string", "comment", "property", "interface"]

LSSOURCE = "stormgls"

CWD = pathlib.Path(__file__).parent.absolute()

class TokenModifier(enum.IntFlag):
    deprecated = enum.auto()
    readonly = enum.auto()
    defaultLibrary = enum.auto()
    definition = enum.auto()


# Lie to the Cmd objects *just* a tiny bit to use a unified interface to get Cmd arguments
class FakeSnap:
    def __init__(self):
        self.lines = []

    def printf(self, mesg):
        self.lines.append(mesg)


class FakeRunt:
    def __init__(self, model):
        self.snap = FakeSnap()
        self.model = model
        self.printf = self.snap.printf


def posToRange(pos):
    return types.Range(
        start=types.Position(line=pos['lines'][1] - 1, character=pos['columns'][0] - 1),
        end=types.Position(line=pos['lines'][1] - 1, character=pos['columns'][1] - 1)
    )


def makeDiagnoticMesg(mesg, pos):
    return types.Diagnostic(
        message=mesg,
        severity=types.DiagnosticSeverity.Warning,
        range=posToRange(pos),
        source=LSSOURCE
    )


class StormLanguageServer(LanguageServer):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.diagnostics = {}
        self.query = None
        self.completions = {
            'version': s_version.verstring,
            'libs': {},
            'formtypes': {},
            'props': {},
            'cmds': {},
            'functions': {}
        }
        self.tkns = []

    def getFuncDefs(self, query):
        '''
        Scan through a parsed query and pull out top level function defs,
        number of args, etc so we can shovel that into the onhover handlers,
        and do arg count checking
        '''
        warnings = []
        for kid in query.kids:
            # (name, args, body)
            if isinstance(kid, s_ast.Function):
                args = []
                name = kid.kids[0].getAstText()
                pos = kid.getPosInfo()

                # This doubles up on first definition?
                # TODO: need to clear out the completions functions every time
                if name in self.completions['functions']:
                    olddef = self.completions['functions'][name]
                    pos['lines'] = (pos['lines'][0], pos['lines'][0])
                    warnings.append(makeDiagnoticMesg(f'function {name} is already defined on line {olddef["start"]}', pos))
                    continue

                # mandatory args are before any CallKwarg structures
                for arg in kid.kids[1]:
                    info = {
                        'name': arg.getAstText()
                    }

                    if isinstance(arg, s_ast.CallKwarg):
                        # TODO: doesn't have the leading $
                        info['default'] = arg.kids[1].getAstText()
                    args.append(info)

                self.completions['functions'][name] = {
                    'body': kid.kids[2].getAstText(),
                    'start': pos['lines'][0],
                    'end': pos['lines'][1],
                    'args': args
                }

        return warnings

    def varcheck(self):
        '''
        TODO: The right way to do this is via constructing a CFG and rolling backwards to do liveness
        checks
        '''
        pass

    def funcCheck(self, kid):
        warnings = []

        func = f'${kid.kids[0].getAstText()}'
        if (func in ('$lib.print', '$lib.warn')) and len(kid.kids[1:]) >= 2:
            # CallKwargs are always index 2
            if len(kid.kids[2].kids) > 0:
                pos = kid.kids[0].getPosInfo()
                warnings.append(makeDiagnoticMesg('Prefer backtick format strings', pos))
        elif func == '$lib.list':
            pos = kid.kids[0].getPosInfo()
            warnings.append(makeDiagnoticMesg(f'{func} is deprecated. Prefer `([])`.', pos))
        elif func == '$lib.dict':
            pos = kid.kids[0].getPosInfo()
            warnings.append(makeDiagnoticMesg(f'{func} is deprecated. Prefer `({{}})`.', pos))
        elif func in self.completions['libs']:
            if self.completions['libs'][func].get('deprecated') is True:
                pos = kid.kids[0].getPosInfo()
                warnings.append(makeDiagnoticMesg(f'{func} is deprecated', pos))

        return warnings

    def cleanCheck(self, query):
        '''
        TODO: clean this up as more of our general "walk all the trees" function
        '''
        warnings = []

        todo = []
        todo.extend(query.kids)

        # TODO: clean this up and use list.indexOf instead of hardcoding type values
        while todo:
            kid = todo.pop(0)
            try:
                pos = kid.getPosInfo()
            except:
                continue

            if isinstance(kid, s_ast.VarDeref):
                text = kid.getAstText()
                if text in JSONEXPR:
                    part = text.split('.')[1]
                    warnings.append(makeDiagnoticMesg(f'Prefer JSON Expression syntax `({part})` over `${text}`', pos))
                    self.tkns.append(((pos['lines'][0], pos['columns'][0]), kid, 0))

                elif (lib := self.completions['libs'].get('$' + text)) is not None:
                    rtype = lib['type']
                    if isinstance(rtype, dict):
                        if rtype['type'] != 'function':
                            self.tkns.append(((pos['lines'][0], pos['columns'][0]), kid, 0))

            else:
                if isinstance(kid, s_ast.FuncCall):
                    warnings.extend(self.funcCheck(kid))
                    text = kid.getAstText()
                    # offset the kid iter by 1
                    if text.startswith('lib.'):
                        self.tkns.append(((pos['lines'][0], pos['columns'][0]), kid.kids[0], 2))
                elif isinstance(kid, s_ast.LiftPropBy):
                    text = kid.kids[0].getAstText()
                    self.tkns.append(((pos['lines'][0], pos['columns'][0]), kid.kids[0], 8))
                elif isinstance(kid, s_ast.LiftProp):
                    text = kid.kids[0].getAstText()
                    self.tkns.append(((pos['lines'][0], pos['columns'][0]), kid.kids[0], 8))
                elif isinstance(kid, s_ast.EditNodeAdd):
                    text = kid.kids[0].getAstText()
                    self.tkns.append(((pos['lines'][0], pos['columns'][0]), kid.kids[0], 8))
                elif isinstance(kid, s_ast.EditEdgeAdd):
                    edgename = kid.kids[0].getAstText()
                    n2 = kid.kids[1]
                    if isinstance(n2, s_ast.SubQuery):
                        query = n2.kids[0]
                        if isinstance(query.kids[0], s_ast.YieldValu):
                            if isinstance(query.kids[0].kids[0], s_ast.VarValue):
                                vartext = query.kids[0].kids[0].getAstText()
                                if kid.n2 is True:
                                    warnings.append(makeDiagnoticMesg(f'Prefer `<({edgename})+ ${vartext}` over `<({edgename})+ {{ yield ${vartext} }}`', pos))
                                else:
                                    warnings.append(makeDiagnoticMesg(f'Prefer `+({edgename})> ${vartext}` over `+({edgename})> {{ yield ${vartext} }}`', pos))
                elif isinstance(kid, s_ast.Return) and kid.kids:
                    text = kid.kids[0].getAstText()
                    pos = kid.kids[0].getPosInfo()
                    if text == 'lib.null':
                        warnings.append(makeDiagnoticMesg('Prefer `return()` over `return($lib.null)`', pos))

            for k in kid.kids:
                todo.append(k)

        return warnings

    def parse(self, document: TextDocument):
        diagnostics = []
        self.completions['functions'] = {}
        self.tkns = []

        try:
            query = s_parser.parseQuery(document.source)
            self.query = query
            diagnostics.extend(self.getFuncDefs(query))
            # cleanliness is next to godliness
            diagnostics.extend(self.cleanCheck(query))
        except s_exc.BadSyntax as e:
            items = e.items()
            token = items.get('token', '1')
            message = items['mesg']
            severity = types.DiagnosticSeverity.Error
            diagnostics.append(
                types.Diagnostic(
                    message=message,
                    severity=severity,
                    range=types.Range(
                        start=types.Position(line=items['line'] - 1, character=items['column'] - 1),
                        end=types.Position(line=items['line'] - 1, character=items['column'] + len(token)),
                    ),
                    source=LSSOURCE,
                )
            )

        self.diagnostics[document.uri] = (document.version, diagnostics)


server = StormLanguageServer("storm-language-server", "v1")


def _getHoverInfo(ls, word):
    # TODO: we could propbably use the position info to comb through the AST
    # to see if we're in a edit block (or part of one) to give docs and autocomplete
    # for RelProps
    if word[0] == '$':
        libs = ls.completions.get('libs')
        if libs and word in libs:
            return 'libs', libs[word]

    # full form:prop info
    props = ls.completions.get('props')
    if props and word in props:
        return 'props', props[word]

    # get form info
    forms = ls.completions.get('formtypes')
    if forms and word in forms:
        return 'formtypes', forms[word]

    # get cmd info
    cmds = ls.completions.get('cmds')
    if cmds and word in cmds:
        return 'cmds', cmds[word]

    funcs = ls.completions.get('functions')
    if funcs and word in funcs:
        return 'function', funcs[word]


@server.feature(types.TEXT_DOCUMENT_HOVER)
async def hover(ls: StormLanguageServer, params: types.HoverParams):
    doc = ls.workspace.get_text_document(params.text_document.uri)

    if params.position is None:
        return

    line = params.position.line
    atCursor = wordAtCursor(line, doc.lines[line], params.position.character)

    if not atCursor:
        return

    word, rng = atCursor

    if word[0] == ':':
        word = word[1:]
    hinfo = _getHoverInfo(ls, word)

    if not hinfo:
        return

    typ, info = hinfo

    rtype = info.get('type')
    desc = info.get('doc')

    if desc:
        lines = [
            word,
            '\n'
        ]
        if isinstance(rtype, dict):
            lines.extend(s_autodoc.prepareRstLines(desc))
            lines.append('\n')
            lines.extend(s_autodoc.runtimeGetArgLines(rtype))
            lines.extend(s_autodoc.runtimeGetReturnLines(rtype))
        elif typ == 'formtypes':
            lines.extend(s_autodoc.prepareRstLines(desc))
            lines.append('\n')
            lines.append('Props:')
            for propname, propinfo in info['props'].items():
                proptype, opts = propinfo['type']
                propline = f'    :{propinfo["name"]}=<{proptype}>'
                lines.append(propline)
                lines.append(f'        {propinfo["doc"]}')
        elif typ == 'props':
            lines.extend(s_autodoc.prepareRstLines(desc))
            lines.append('\n')
            proptype, opts = info['type']
            lines.extend(s_autodoc.prepareRstLines(f'Type: {proptype}'))
            lines.extend(s_autodoc.prepareRstLines(f'Opts: {opts}'))
        elif typ == 'cmds':
            lines.append(info.get('help'))

        return types.Hover(
            # TODO: is there an RST type?
            contents=types.MarkupContent(
                kind=types.MarkupKind.Markdown,
                value='\n'.join(lines),
            ),
            range=types.Range(
                start=types.Position(line=line, character=0),
                end=types.Position(line=line+1, character=0)
            )
        )


@server.feature(types.TEXT_DOCUMENT_DID_OPEN)
@server.feature(types.TEXT_DOCUMENT_DID_SAVE)
@server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
async def did_change(ls: StormLanguageServer, params: types.DidOpenTextDocumentParams):
    doc = ls.workspace.get_text_document(params.text_document.uri)
    ls.parse(doc)

    for uri, (version, diagnostics) in ls.diagnostics.items():
        ls.publish_diagnostics(uri=uri, version=version, diagnostics=diagnostics)


@server.feature(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
async def document_symbol(ls: StormLanguageServer, params: types.DocumentSymbolParams):
    if not ls.query:
        return None

    retn = []

    # Top level pass for globals and functions
    for kid in ls.query.kids:
        await asyncio.sleep(0)
        if isinstance(kid, s_ast.Function):
            pos = kid.getPosInfo()
            retn.append(
                types.DocumentSymbol(
                    name=kid.kids[0].value(),
                    kind=types.SymbolKind.Function,
                    range=types.Range(
                        start=types.Position(line=pos['lines'][0]-1, character=0),
                        end=types.Position(line=pos['lines'][1]-1, character=0),
                    ),
                    selection_range=types.Range(
                        start=types.Position(line=pos['lines'][0]-1, character=0),
                        end=types.Position(line=pos['lines'][1]-1, character=0),
                    )
                )
            )
        elif isinstance(kid, s_ast.SetVarOper):
            pos = kid.getPosInfo()
            retn.append(
                types.DocumentSymbol(
                    name=kid.kids[0].value(),
                    kind=types.SymbolKind.Variable,
                    range=types.Range(
                        start=types.Position(line=pos['lines'][0]-1, character=0),
                        end=types.Position(line=pos['lines'][1]-1, character=0),
                    ),
                    selection_range=types.Range(
                        start=types.Position(line=pos['lines'][0]-1, character=0),
                        end=types.Position(line=pos['lines'][1]-1, character=0),
                    )
                )
            )

    return retn


@server.feature(
    types.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL,
    types.SemanticTokensLegend(
        token_types=TokenTypes,
        token_modifiers=[m.name for m in TokenModifier],
    )
)
async def semantic_tokens(ls: StormLanguageServer, params: types.SemanticTokensParams):
    '''
    This looks awkward. Let me explain. The token information is just a big 1D list
    of integers that only have meaning in groups of 5 (and no, it's not a list of lists)

    TODO: Honestly if I expand this enough, I could just deprecate vim-storm entirely....
    it would solve some of the nested edit block issues vim-storm has

    On seonc thought, it can still fix the edit block stuff, but since lark doesn't
    really spit all the lexed items, it skips all the hard coded keywords like "function"
    '''

    tkns = []
    prevLine = 0
    prevOffs = 0
    stkns = sorted(ls.tkns, key=lambda k: k[0])
    for (line, offs), tkn, type in stkns:
        line -= 1
        offs -= 1
        if line != prevLine:
            prevOffs = 0
        txt = tkn.getAstText()

        flags = 0
        if info := ls.completions['libs'].get(f'${txt}'):
            flags = 4
            if not isinstance(info['type'], dict):
                type = 1
            if info.get('deprecated', False) is True:
                flags |= 1
        elif info := ls.completions['formtypes'].get(txt):
            if info.get('deprecated', False) is True:
                flags |= 1
        elif info := ls.completions['props'].get(txt):
            if info.get('deprecated', False) is True:
                flags |= 1
        else:
            if type != 8:
                flags = 1

        valu = [
            line - prevLine,
            offs - prevOffs,
            len(txt),
            type,
            flags
        ]
        prevLine = line
        prevOffs = offs
        tkns.extend(valu)

    return types.SemanticTokens(data=tkns)


@contextlib.asynccontextmanager
async def getTestCore():

    # Import this here to save some import times on startup
    import synapse.cortex as s_cortex

    # It's an annoying startup cost, but it's a pretty dumb simple way to get the default model defs
    # TODO: so if we had a cortex connection we could reach out and also autocomplete
    # package names and stormcmds, non-default model elements, but that might be a tad touchy to do
    # because I don't wanna touch cred storing
    conf = {
        'health:sysctl:checks': False,
    }
    # Defer pulling in the autocompletes because start_io starts its own asyncio loop which causes
    # issues if we start our own
    with tempfile.TemporaryDirectory() as dirn:
        async with await s_cortex.Cortex.anit(dirn, conf=conf) as core:
            yield core


async def loadCompletions(core):
    # TODO: Maybe just change these to properties of the server?
    # TODO: Also separate out interfaces?
    completions = {
        'version': s_version.verstring,
        'libs': {},
        'formtypes': {},
        'props': {},
        'cmds': {},
        'functions': {}
    }
    for (path, lib) in s_stormtypes.registry.iterLibs():
        base = '.'.join(('lib',) + path)
        libdepr = lib._storm_lib_deprecation is not None
        for lcl in lib._storm_locals:
            name = lcl['name']
            # TODO: clean up $lib vs lib usage/keying
            key = '$' + '.'.join((base, name))
            lcldepr = lcl.get('deprecated')
            depr = libdepr
            if lcldepr:
                if lcldepr.get('eolvers') or lcldepr.get('eoldate'):
                    depr = True
            type = lcl['type']
            info = {
                'doc': lcl.get('desc'),
                'type': type,
                'deprecated': depr
            }

            if isinstance(type, dict) and type.get('type') == 'function':
                args = type.get('args', ())
                info['args'] = args
                info['retn'] = type['returns']

            completions['libs'][key] = info

    model = await core.getModelDict()

    for formtype, typeinfo in model.get('types', {}).items():
        completions['formtypes'][formtype] = {
            'doc': typeinfo['info'].get('doc', ''),
            'deprecated': typeinfo['info'].get('deprecated', False)
        }

    for form, info in model.get('forms', {}).items():
        if not completions['formtypes'][form].get('props'):
            completions['formtypes'][form]['props'] = {}

        for propname, propinfo in info['props'].items():
            full = propinfo['full']
            completions['formtypes'][form]['props'][propname] = propinfo
            completions['props'][full] = {
                'doc': propinfo.get('doc', ''),
                'deprecated': propinfo.get('deprecated', False),
                'type': propinfo.get('type', {})
            }

    fake = FakeRunt(model)
    for name, ctor in core.stormcmds.items():
        doc = ctor.getCmdBrief()
        cmd = ctor(fake, True)
        argp = cmd.getArgParser()
        argp.help()
        completions['cmds'][name] = {
            'doc': doc,
            # TODO: I don't believe we have any deprecated commands?
            'deprecated': False,
            'help': '\n'.join(argp.mesgs)
        }

    return completions


async def saveCompletions(path):
    async with getTestCore() as core:
        completions = await loadCompletions(core)
        path.write_bytes(s_msgpack.en(completions))

    return completions


@server.feature(types.WORKSPACE_DID_CHANGE_CONFIGURATION)
async def didChangeConfiguration(ls: StormLanguageServer, params: types.DidChangeConfigurationParams):
    # This is mostly here to prevent an error message in the lsp log
    pass

@server.feature(types.INITIALIZE)
async def lsinit(ls: StormLanguageServer, params: types.InitializeParams):
    '''
    NOTE: We absolutely cannot have this coroutine yield the IO loop *at all*
    other other LSP handlers will be allowed to run (like the semantic highlighter)
    which can lead to annoying cases where a valid lib function could be marked as
    not existing even though it totally does, all because loadCompletions has not
    finished running
    '''
    config = await ls.get_configuration_async(
        types.ConfigurationParams(
            items=[
                types.ConfigurationItem(section='datadir')
            ]
        )
    )

    if not config or not config[0]:
        datadir = CWD
    else:
        datadir = pathlib.Path(config[0])

    ls.show_message_log(f'Loading completions from {datadir}')

    cache = (datadir / 'completions.mpk').absolute()

    if not cache.exists() or cache.stat().st_size == 0:
        completions = await saveCompletions(cache)

    else:
        # ls.show_message(f'Loading {cache} {cache.stat().st_size}')
        completions = s_msgpack.un(cache.read_bytes())

        if (version := completions['version']) != s_version.verstring:
            ls.show_message(f'Updating completion cache from {version} to {s_version.verstring}')
            completions = await saveCompletions(cache)

    ls.completions = completions

    ls.show_message('storm ready')


def wordAtCursor(lineNum, line, charAt):
    for match in WORD.finditer(line):
        start = match.start()
        end = match.end()
        if start <= charAt <= end:
            return (line[start:end], types.Range(
                start=types.Position(line=lineNum, character=start),
                end=types.Position(line=lineNum, character=end),
            ))

    return None


@server.feature(types.TEXT_DOCUMENT_COMPLETION, types.CompletionOptions(trigger_characters=[".", ':']))
async def autocomplete(ls: StormLanguageServer, params: types.CompletionParams):
    uri = params.text_document.uri
    doc = ls.workspace.get_text_document(uri)

    if params.position is None:
        return

    line = params.position.line
    atCursor = wordAtCursor(line, doc.lines[line], params.position.character)

    retn = []
    depr = [types.CompletionItemTag.Deprecated,]
    # TODO: we can probably avoid the default object construction on a lot of these
    if atCursor:
        word, rng = atCursor
        if word[0] == '$':
            for name, valu in ls.completions.get('libs', {}).items():
                if name.startswith(word):
                    kind = types.CompletionItemKind.Property
                    if isinstance(valu.get('type'), dict):
                        if valu['type'].get('type') == 'function':
                            kind = types.CompletionItemKind.Function
                    retn.append(
                        types.CompletionItem(
                            label=name,
                            kind=kind,
                            detail=valu.get('doc'),
                            text_edit=types.TextEdit(
                                new_text=name,
                                range=rng,
                            ),
                            tags=[] if not valu.get('deprecated', False) else depr
                        )
                    )
            for name, valu in ls.completions.get('functions', {}).items():
                name = f'${name}'
                if name.startswith(word):
                    kind = types.CompletionItemKind.Function
                    retn.append(
                        types.CompletionItem(
                            label=name,
                            kind=kind,
                            # Maybe the detail should be the function body? Feels kinda excessive
                            text_edit=types.TextEdit(
                                new_text=name,
                                range=rng,
                            ),
                        )
                    )
                start = valu['start']
                end = valu['end']
                if start <= line < end:
                    args = valu['args']
                    # TODO: we could also recurse down and find any SetVar opers?
                    # TODO: Like the issue noted later with commands, we could add our own completion
                    # type here for parameter (or perhaps that's better left to semantic highlighting?)

                    for arg in args:
                        argname = f"${arg['name']}"
                        if argname.startswith(word):
                            retn.append(
                                types.CompletionItem(
                                    label=argname,
                                    kind=types.CompletionItemKind.Variable,
                                    text_edit=types.TextEdit(
                                        new_text=argname,
                                        range=rng,
                                    )
                                )
                            )

            # TODO: detect what function we're in and populate variables based on that
            # TODO: also add global variables to this

        else:
            text = word.strip()

            # if it's dumb but it works, how dumb is it really?
            formtypes = ls.completions.get('formtypes', {})
            for name, valu in formtypes.items():
                if name.startswith(text):
                    retn.append(
                        types.CompletionItem(
                            label=name,
                            kind=types.CompletionItemKind.Field,
                            detail=valu.get('doc', ''),
                            text_edit=types.TextEdit(
                                new_text=name,
                                range=rng,
                            ),
                            tags=[] if not valu.get('deprecated', False) else depr
                            # tags=[types.CompletionItemTag.Deprecated]
                        )
                    )
            props = ls.completions.get('props', {})
            for name, valu in props.items():
                if name.startswith(text):
                    retn.append(
                        types.CompletionItem(
                            label=name,
                            kind=types.CompletionItemKind.Property,
                            detail=valu.get('doc', ''),
                            text_edit=types.TextEdit(
                                new_text=name,
                                range=rng,
                            ),
                            tags=[] if not valu.get('deprecated', False) else depr
                        )
                    )

            cmds = ls.completions.get('cmds', {})
            for name, valu in cmds.items():
                if name.startswith(text):
                    # TODO: as part of the LS protocol python pack we could define a custom type
                    # and use that here, but it's not yet in a proper release, so for now we
                    # gotta go with something not as accurate.
                    retn.append(
                        types.CompletionItem(
                            label=name,
                            kind=types.CompletionItemKind.Function,
                            detail=valu.get('doc', ''),
                            text_edit=types.TextEdit(
                                new_text=name,
                                range=rng
                            ),
                            tags=[] if not valu.get('deprecated', False) else depr
                        )
                    )

    return types.CompletionList(is_incomplete=False, items=retn)


def main(argv):
    server.start_io()


if __name__ == '__main__':
    main(sys.argv[1:])
