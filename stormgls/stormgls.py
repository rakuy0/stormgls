import re
import sys
import tempfile
import contextlib

import synapse.exc as s_exc
import synapse.cortex as s_cortex

import synapse.lib.ast as s_ast
import synapse.lib.parser as s_parser
import synapse.lib.autodoc as s_autodoc
import synapse.lib.stormtypes as s_stormtypes

from pygls.server import LanguageServer
from pygls.workspace import TextDocument

from lsprotocol import types

WORD = re.compile(r'\$?[\w\:\.]+')

# Lie to the Cmd objects *just* a tiny bit to use a unified interface to get Cmd arguments
class FakeSnap:
    def __init__(self):
        self.lines = []

    def printf(self, mesg):
        self.lines.append(mesg)

class FakeRunt:
    def __init__(self):
        self.snap = FakeSnap()

class StormLanguageServer(LanguageServer):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.diagnostics = {}
        self.query = None
        self.completions = {}

    async def loadCompletions(self, core):
        self.completions = {
            'libs': {},
            'formtypes': {},
            'props': {},
            'cmds': {},
        }

        for (path, lib) in s_stormtypes.registry.iterLibs():
            base = '.'.join(('lib',) + path)
            libdepr = lib._storm_lib_deprecation is not None
            for lcl in lib._storm_locals:
                name = lcl['name']
                key = '$' + '.'.join((base, name))
                lcldepr = lcl.get('deprecated')
                depr = libdepr
                if lcldepr:
                    if lcldepr.get('eolvers') or lcldepr.get('eoldate'):
                        depr = True
                self.completions['libs'][key] = {
                    'doc': lcl.get('desc'),
                    'type': lcl['type'],
                    'deprecated': depr
                }

        model = await core.getModelDict()

        for formtype, typeinfo in model.get('types', {}).items():
            self.completions['formtypes'][formtype] = {
                'doc': typeinfo['info'].get('doc', ''),
                'deprecated': typeinfo['info'].get('deprecated', False)
            }

        for form, info in model.get('forms', {}).items():
            if not self.completions['formtypes'][form].get('props'):
                self.completions['formtypes'][form]['props'] = {}

            for propname, propinfo in info['props'].items():
                full = propinfo['full']
                self.completions['formtypes'][form]['props'][propname] = propinfo
                self.completions['props'][full] = {
                    'doc': propinfo.get('doc', ''),
                    'deprecated': propinfo.get('deprecated', False),
                    'type': propinfo.get('type', {})
                }

        fake = FakeRunt()
        for name, ctor in core.stormcmds.items():
            doc = ctor.getCmdBrief()
            cmd = ctor(fake, True)
            argp = cmd.getArgParser()
            argp.help()
            self.completions['cmds'][name] = {
                'doc': doc,
                # TODO: I don't believe we have any deprecated commands?
                'deprecated': False,
                'help': '\n'.join(argp.mesgs)
            }

    def parse(self, document: TextDocument):
        diagnostics = []

        try:
            query = s_parser.parseQuery(document.source)
            self.query = query
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

    for kid in ls.query.kids:
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


@contextlib.asynccontextmanager
async def getTestCore():
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


@server.feature(types.INITIALIZE)
async def lsinit(ls: StormLanguageServer, params: types.InitializeParams):
    async with getTestCore() as core:
        await ls.loadCompletions(core)
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

            # TODO: detect what function we're in and populate variables based on that
            # also add global variables to this
            for kid in ls.query.kids:
                if isinstance(kid, s_ast.Function):
                    name = f'${kid.kids[0].value()}'
                    if name.startswith(word):
                        retn.append(
                            types.CompletionItem(
                                label=name,
                                kind=types.CompletionItemKind.Function,
                                text_edit=types.TextEdit(
                                    new_text=name,
                                    range=rng,
                                )
                            )
                        )

                    pos = kid.getPosInfo()
                    start, end = pos['lines']
                    if start <= line < end:
                        # TODO: we could also recurse down and find any SetVar opers?
                        funcargs = [f'${p.value()}' for p in kid.kids[1].kids]
                        # TODO: Like the issue noted later with commands, we could add our own completion
                        # type here for parameter (or perhaps that's better left to semantic highlighting?)
                        for arg in funcargs:
                            if arg.startswith(word):
                                retn.append(
                                    types.CompletionItem(
                                        label=arg,
                                        kind=types.CompletionItemKind.Variable,
                                        text_edit=types.TextEdit(
                                            new_text=arg,
                                            range=rng,
                                        )
                                    )
                                )

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

            # TODO: Keywords?

    return types.CompletionList(is_incomplete=False, items=retn)


def main(argv):
    server.start_io()


if __name__ == '__main__':
    main(sys.argv[1:])
