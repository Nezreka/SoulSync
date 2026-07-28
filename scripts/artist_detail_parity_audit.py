#!/usr/bin/env python3
"""Continuous parity audit: the port vs library.js. Run after every change."""
import re, sys
from pathlib import Path

ROOT = Path("/mnt/e/Broque Projects/Github Projects/SoulSync")

def strip(s):
    out=list(s); i,n,mode=0,len(s),None
    while i<n:
        c=s[i]; two=s[i:i+2]
        if mode is None:
            if two=='//': mode='line'; out[i]=out[i+1]=' '; i+=2; continue
            if two=='/*': mode='block'; out[i]=out[i+1]=' '; i+=2; continue
            if c in '"\'`': mode=c; i+=1; continue
            i+=1
        elif mode=='line':
            if c=='\n': mode=None
            else: out[i]=' '
            i+=1
        elif mode=='block':
            if two=='*/': out[i]=out[i+1]=' '; mode=None; i+=2; continue
            if c!='\n': out[i]=' '
            i+=1
        else:
            if c=='\\': i+=2; continue
            if c==mode: mode=None
            i+=1
    return ''.join(out)

VANILLA = strip((ROOT/"webui/static/library.js").read_text(encoding="utf-8"))
PORT = "\n".join(p.read_text(encoding="utf-8")
                 for p in (ROOT/"webui/src/routes/artist-detail").rglob("*.ts*"))

def body(name):
    try: i = VANILLA.index(f"function {name}(")
    except ValueError: return None
    rest = VANILLA[i:]
    m = re.search(r"\n(?:async )?function ", rest[1:])
    return rest[:m.start()+1] if m else rest

# functions claimed ported -> (their vanilla name)
PORTED = ["loadArtistDetailData","populateArtistDetailPage","updateArtistHeroSection",
          "updateArtistDetailPageHeaderWithData","renderArtistEnrichmentCoverage",
          "updateArtistSummaryStats","updateCategoryStats","updateArtistGenres",
          "createReleaseCard","populateReleaseSection","populateDiscographySections",
          "applyDiscographyFilters","_classifyReleaseContent","resetDiscographyFilters",
          "initializeDiscographyFilters",
          # Enhanced Management view
          "toggleEnhancedView","loadEnhancedViewData","renderEnhancedView","renderEnhancedStatsBar",
          "renderEnhancedSection","renderAlbumRow","renderExpandedAlbumHeader","renderAlbumMetaRow",
          "renderTrackTable","_buildTrackRow","_attachTableDelegation","_getEnhancedAlbumTrackRows",
          "_normalizeExpectedMissingTrack","_deriveEnhancedMissingTracks","sortEnhancedTracks",
          "saveAlbumMetadata","startInlineEdit","saveInlineEdit","updateBulkBar",
          "showBulkEditModal","executeBulkEdit","batchAnalyzeReplayGainSelected",
          "batchWriteTagsSelected","clearTrackSelection","extractFormat","formatDurationMs",
          "getServiceUrl","makeClickableBadge","_getEnhancedAlbumCanonicalSource",
          # DB record inspector + top tracks + gap-fill
          "setupArtistRecordButton","_arecRenderFields","_arecApplyFilter",
          "_loadArtistTopTracks","playTrackByMetadata","_topTrackDownloadOne","_topTrackDownloadAll",
          "_gapFillEnabled","_gapNorm","_gapYear","_gapSameRelease","_loadDiscographyGapFill",
          "_streamGapOwnership","checkLibraryCompletion","updateLibraryReleaseCard",
          "updateCategoryStatsFromStream","recalculateSummaryStats","_libraryViewModeKey",
          "isEnhancedAdmin","_trackSlotKey","_normTitleForMatch"]

SKIP_FIELDS = {"map","filter","some","forEach","length","push","find","join","split","replace",
               "trim","toString","toLowerCase","includes","slice","concat","every","sort","style",
               "classList","dataset","textContent","innerHTML","appendChild","querySelector",
               "querySelectorAll","remove","src","alt","onclick","onerror","onload","id","className",
               "title","type","parentElement","closest","value","checked","disabled","display"}

# Cross-file globals that are NOT gaps. Each needs a reason; an audit that is
# permanently red gets ignored, and "deferred" must mean deferred-with-a-plan.
DEFERRED = {
    "_esc": "createReleaseCard uses a LOCAL _esc; React escapes text automatically",
    "showToast": "invoked via window.showToast from the page component (not yet built)",
    "hideLoadingOverlay": "release-open flow, page component",
    "showLoadingOverlay": "release-open flow, page component",
    "openAddToWishlistModal": "stays vanilla; invoked from the page component",
    "lazyLoadTrackOwnership": "stays vanilla; invoked after the modal opens",
    "checkArtistEnhanceEligibility": "fire-and-forget after load, page component",
    "loadSimilarArtists": "stays vanilla; invoked from the page component",
    "getAudioDBLogoURL": "deferred to via window in audioDbLogoUrl()",
    "escapeHtml": "only used to escape an error message into innerHTML; React escapes text",
    "_escAttr": "only used to escape values into an innerHTML string; React escapes attributes",
}

problems = []

# 1. fields the vanilla reads that the port never mentions
fields = {}
for name in PORTED:
    b = body(name)
    if b is None:
        problems.append(f"vanilla function vanished: {name}")
        continue
    for obj in ("artist","data","discography","release","enrichment"):
        for f in re.findall(rf"\b{obj}\.(\w+)", b):
            if f not in SKIP_FIELDS:
                fields.setdefault(f"{obj}.{f}", set()).add(name)
missing = {k: v for k, v in fields.items() if k.split(".")[1] not in PORT}
if missing:
    problems.append(f"{len(missing)} field(s) read by the vanilla, absent from the port:")
    for k, v in sorted(missing.items()):
        problems.append(f"    {k:34} read in {', '.join(sorted(v))}")

# 2. globals the vanilla calls that live OUTSIDE library.js and the port must invoke
OTHER = strip("\n".join(p.read_text(encoding="utf-8", errors="replace")
              for p in (ROOT/"webui/static").glob("*.js") if p.name != "library.js"))
other_fns = set(re.findall(r"^(?:async )?function ([A-Za-z_$][\w$]*)", OTHER, re.M))
for name in PORTED:
    b = body(name)
    if not b: continue
    for called in set(re.findall(r"\b([a-zA-Z_$][\w$]*)\(", b)):
        if called in DEFERRED: continue
        if called in other_fns and called not in PORT and called not in SKIP_FIELDS:
            problems.append(f"    calls {called}() from another static file — not referenced in the port ({name})")

if problems:
    print("PARITY AUDIT — issues:")
    for p in problems: print(" ", p)
    sys.exit(1)
print("PARITY AUDIT — clean")
print(f"  ({len(DEFERRED)} cross-file calls deferred with reasons — see DEFERRED)")
