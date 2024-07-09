import sys
import logging
import tempfile
import contextlib

import synapse.exc as s_exc
import synapse.cortex as s_cortex

import synapse.lib.ast as s_ast
import synapse.lib.parser as s_parser
import synapse.lib.stormtypes as s_stormtypes

from pygls.server import LanguageServer
from pygls.workspace import TextDocument

from lsprotocol import types

logging.basicConfig(filename='pygls.log', filemode='w', level=logging.DEBUG)

server = LanguageServer("storm-glass-server", "v0.0.1")

"""
@server.feature(
    lsp.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL,
    lsp.SemanticTokensLegend(token_types=["function"], token_modifiers=[]),

)
def semantic_tokens(ls, params):
    '''
    This is what LSP uses for things like syntax highlighting...sorta
    '''
    ls.show_message("Starting semantic tokens")
    data = []

    uri = params.text_document.uri
    doc = ls.workspace.get_document(uri)

    last_line = 0
    last_start = 0

    data = []

    for lineno, line in enumerate(doc.lines):
        last_start = 0

        for matcher in TOKENS:
            for match in matcher.finditer(line):
                start, end = match.span()
                data += [(lineno - last_line), (start - last_start), (end - start), 0, 0]

                last_line = lineno
                last_start = start

    ls.show_message(str(data))
    return lsp.SemanticTokens(data=data)
"""

class StormLanguageServer(LanguageServer):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.diagnostics = {}
        self.query = None
        self.completions = {}

    async def loadCompletions(self, core):
        self.completions = {
            'libs': {},
            'types': {},
            'model': {},
        }
        for lib in s_stormtypes.registry.getLibDocs():
            base = '.'.join(lib['path'])
            for lcl in lib['locals']:
                name = lcl['name']
                key = '.'.join((base, name))
                self.completions['libs'][key] = lcl

        self.libs = {
            'libs': s_stormtypes.registry.getLibDocs(),
            # 'types': s_stormtypes.registry.getTypeDocs(),
            'model': await core.getModelDict()
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
                        start=types.Position(line=items['line'], character=items['column']-1),
                        end=types.Position(line=items['line'], character=items['column'] + len(token)),
                    ),
                )
            )

        self.diagnostics[document.uri] = (document.version, diagnostics)


server = StormLanguageServer("diagnostic-server", "v1")


# TODO: Maybe change to on save since parsing isn't instant?
@server.feature(types.TEXT_DOCUMENT_DID_OPEN)
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

    return retn


@contextlib.asynccontextmanager
async def getTestCore():
    # It's an annoying startup cost, but it's a pretty dumb simple way to get the default model defs
    # TODO: so if we had a cortex connection we could reach out and also autocomplete
    # package names and stormcmds, non-default model elements, but that might be a tad touchy to do.
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


def wordAtCursor(line, lineNo, charAt):
    # roll backwards until we hit a space or the start of the line
    start = charAt
    while start > 0:
        if line[start] == ' ' or line[start] == '\t' or line[start] == '$':
            start += 1
            break
        start -= 1

    return (
        line[start:charAt],
        types.Range(
            start=types.Position(line=lineNo, character=start),
            end=types.Position(line=lineNo, character=charAt),
        ),
    )


@server.feature(types.TEXT_DOCUMENT_COMPLETION, types.CompletionOptions(trigger_characters=[".", ':']))
#@server.feature(types.TEXT_DOCUMENT_COMPLETION, types.CompletionOptions())
async def autocomplete(ls: StormLanguageServer, params: types.CompletionParams):
    uri = params.text_document.uri
    doc = ls.workspace.get_document(uri)

    if params.context is not None:
        pass

    word = wordAtCursor(doc.lines[params.position.line], params.position.line, params.position.character)

    retn = []
    if word:
        text, pos = word
        for name, valu in ls.completions.get('libs', []).items():
            if name.startswith(text):
                kind = types.CompletionItemKind.Property
                if isinstance(valu.get('type'), dict):
                    if valu['type']['type'] == 'function':
                        kind = types.CompletionItemKind.Function
                retn.append(
                    types.CompletionItem(
                        label=name,
                        kind=kind,
                        detail=valu['desc'],
                    )
                )
    return types.CompletionList(is_incomplete=False, items=retn)


def main(argv):
    # opts = setup().parse_args(argv)

    server.start_io()


if __name__ == '__main__':
    main(sys.argv[1:])
