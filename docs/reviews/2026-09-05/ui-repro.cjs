// Read-only UI review reproductions. Extracts current functions verbatim via
// TypeScript AST; supplies in-memory data/hooks only. No HTTP or database calls.
const {createRequire} = require('node:module');
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '../../..');
const req = createRequire(path.join(root, 'webui/package.json'));
const ts = req('typescript');
const {JSDOM} = req('jsdom');
const dom = new JSDOM('<!doctype html><html><body></body></html>', {url:'http://localhost'});
global.window = dom.window;
global.document = dom.window.document;
global.HTMLElement = dom.window.HTMLElement;
Object.defineProperty(global, 'navigator', {value:dom.window.navigator, configurable:true});
global.IS_REACT_ACT_ENVIRONMENT = true;
const React = req('react');
const {render, cleanup, act, fireEvent} = req('@testing-library/react');
const rq = req('@tanstack/react-query');
const page = 'webui/src/routes/library/-ui/library-v2-page.tsx';
const api = 'webui/src/routes/library/-library-v2.api.ts';
function extract(file, names) {
  const source = fs.readFileSync(path.join(root,file),'utf8');
  const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const snippets = [];
  for (const name of names) {
    const node = ast.statements.find(s => s.name?.text === name ||
      (ts.isVariableStatement(s) && s.declarationList.declarations.some(d => d.name.getText(ast) === name)));
    assert.ok(node, `Cannot find ${name}`);
    snippets.push(node.getText(ast));
  }
  return snippets.join('\n');
}
function load(source, names, supplied) {
  const code = ts.transpileModule(source, {compilerOptions:{target:ts.ScriptTarget.ES2022,
    module:ts.ModuleKind.CommonJS, jsx:ts.JsxEmit.React}}).outputText;
  const context = {exports:{}, React, ...React, ...rq, console, window, document,
    styles:new Proxy({}, {get:(_,key)=>String(key)}), ...supplied};
  vm.createContext(context);
  vm.runInContext(`${code}\nglobalThis.selected = {${names.join(',')}};`, context);
  return context.selected;
}
const tick = async () => { await act(async()=> {await new Promise(r=>setTimeout(r,25));}); };
async function migration() {
  let state = {running:false, stage:null, current:0, total:0, error:null,
    artwork_cache:{running:false,current:0,total:0,error:null},
    bootstrap:{status:'running',stage:'artists',current:0,total:1}};
  const client = new rq.QueryClient({defaultOptions:{queries:{retry:false,staleTime:10_000,refetchOnWindowFocus:false}}});
  let reads=0;
  let catalogue=[];
  const artistKey=['library-v2','artists','','name','all',1,false];
  const observer=new rq.QueryObserver(client,{queryKey:artistKey,queryFn:async()=>{reads++;return {artists:catalogue};}});
  const unsubscribe=observer.subscribe(()=>{});
  const names=['ImportButton'];
  const extracted=load(extract(page,['ImportButton','clampPercent','mutationErrorMessage','IMPORT_STAGE_LABELS',
    'describeLibraryV2Migration','describeLibraryV2ImportCompletion','describeLibraryV2ImportProgress',
    'describeLibraryV2ArtworkCacheProgress'])+'\n'+extract(api,['libraryV2ImportStatusQueryOptions','invalidateLibraryV2']), names,
    {LIBRARY_V2_QUERY_KEY:['library-v2'],useLibraryV2CanWrite:()=>true,
     fetchLibraryV2ImportStatus:async()=>state,startLibraryV2Import:()=>{throw Error('Unexpected write')}});
  const rendered=render(React.createElement(rq.QueryClientProvider,{client},
    React.createElement(extracted.ImportButton,{hasArtists:false,pollIntervalMs:60_000})));
  await tick();
  assert.match(rendered.container.textContent,/Migrating/);
  assert.equal(reads,1);
  catalogue=[{id:1,name:'Migrated artist'}];
  state={...state,bootstrap:{...state.bootstrap,status:'done',current:1}};
  await act(async()=>{await client.refetchQueries({queryKey:['library-v2','import-status']});});
  await tick();
  assert.equal(reads,1, 'Current defect: artists never refetched');
  assert.equal(client.getQueryData(artistKey).artists.length,0);
  assert.match(rendered.container.textContent,/Import library/);
  console.log('UI-01 reproduced:',JSON.stringify({bootstrap:state.bootstrap.status,artistFetches:reads,
    cachedArtists:client.getQueryData(artistKey).artists.length,actualArtists:catalogue.length,button:rendered.container.textContent}));
  cleanup(); unsubscribe(); client.clear();
}
async function pagination() {
  let search={section:'wanted',q:'',page:2,artist:undefined,album:undefined};
  const {LibrarySectionTabs}=load(extract(page,['LibrarySectionTabs']),['LibrarySectionTabs'],{
    Route:{useSearch:()=>search},useNavigate:()=>({search:update})=>{search=update(search);return Promise.resolve();}});
  const rendered=render(React.createElement(LibrarySectionTabs));
  fireEvent.click(rendered.getByRole('button',{name:'Artists'}));
  assert.equal(search.section,'artists');
  assert.equal(search.page,2,'Current defect: wanted page leaks into artists');
  // API default limit is 75, SQL uses (page-1)*limit without page clamping.
  const artists=Array.from({length:12},(_,id)=>({id}));
  const rows=artists.slice((search.page-1)*75,search.page*75);
  const totalPages=Math.ceil(artists.length/75);
  assert.equal(rows.length,0);
  assert.equal(totalPages>1,false);
  console.log('UI-02 reproduced:',JSON.stringify({search,actualArtists:artists.length,
    returnedRows:rows.length,totalPages,paginationVisible:totalPages>1}));
  cleanup();
}
async function migrationRetry() {
  let state={running:false,artwork_cache:{running:false},bootstrap:{status:'running'}};
  let reads=0;
  const client=new rq.QueryClient({defaultOptions:{queries:{retry:false,refetchOnWindowFocus:false}}});
  const {libraryV2ImportStatusQueryOptions}=load(extract(api,['libraryV2ImportStatusQueryOptions']),
    ['libraryV2ImportStatusQueryOptions'],{LIBRARY_V2_QUERY_KEY:['library-v2'],
      fetchLibraryV2ImportStatus:async()=>{reads++;return state;}});
  function Status() {
    const query=rq.useQuery(libraryV2ImportStatusQueryOptions(20));
    return React.createElement('span',null,query.data?.bootstrap.status);
  }
  const rendered=render(React.createElement(rq.QueryClientProvider,{client},React.createElement(Status)));
  await tick();
  assert.equal(rendered.container.textContent,'running');
  state={...state,bootstrap:{status:'failed',last_error:'transient failure'}};
  for(let i=0;i<5 && rendered.container.textContent!=='failed';i++) await tick();
  assert.equal(rendered.container.textContent,'failed');
  const stoppedAt=reads;
  // Represents the existing server autostart loop retrying successfully.
  state={...state,bootstrap:{status:'running'}};
  for(let i=0;i<6;i++) await tick();
  assert.equal(reads,stoppedAt,'Current defect: failed status stops retry observation');
  assert.equal(rendered.container.textContent,'failed');
  console.log('UI-03 reproduced:',JSON.stringify({serverBootstrap:state.bootstrap.status,
    renderedBootstrap:rendered.container.textContent,statusReadsAfterRetry:reads-stoppedAt}));
  cleanup();client.clear();
}
(async()=>{await migration();await pagination();await migrationRetry();dom.window.close();})().catch(e=>{console.error(e);process.exitCode=1;});
