/* Chhanni - Premiere Pro host script (ExtendScript, ES3) */
var CH = { items: {}, main: null, over: {}, bins: {} };
var TICKS = 254016000000;

function chEsc(s) { return String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, " "); }
function chNorm(p) { return String(p || "").replace(/\//g, "\\").toLowerCase(); }
function chTicks(sec) { return String(Math.round(sec * TICKS)); }
function chOk(extra) { return '{"ok":true' + (extra ? "," + extra : "") + "}"; }
function chErr(e) { return '{"ok":false,"error":"' + chEsc(e && e.message ? e.message + " (line " + e.line + ")" : e) + '"}'; }

function chhanniInfo() {
    try {
        if (!app.project || !app.project.rootItem) return '{"ok":false,"error":"Premiere mein project kholiye"}';
        return chOk('"name":"' + chEsc(app.project.name) + '","version":"' + chEsc(app.version) + '"');
    } catch (e) { return chErr(e); }
}

function chFindBin(parent, name) {
    for (var i = 0; i < parent.children.numItems; i++) {
        var c = parent.children[i];
        if (c && c.type === ProjectItemType.BIN && c.name === name) return c;
    }
    return null;
}
function chGetBin(parent, name) {
    var b = chFindBin(parent, name);
    if (b) return b;
    var r = parent.createBin(name);
    return (r && r.type === ProjectItemType.BIN) ? r : chFindBin(parent, name);
}
function chCollect(bin) {
    for (var i = 0; i < bin.children.numItems; i++) {
        var c = bin.children[i];
        if (!c) continue;
        if (c.type === ProjectItemType.BIN) { chCollect(c); continue; }
        try { var p = c.getMediaPath(); if (p) CH.items[chNorm(p)] = c; } catch (e) {}
    }
}

/* 1. root bin */
function chhanniStart(projectName) {
    try {
        CH.items = {}; CH.over = {}; CH.bins = {};
        var d = new Date();
        var pad = function (n) { return (n < 10 ? "0" : "") + n; };
        var stamp = d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " + pad(d.getHours()) + pad(d.getMinutes());
        var name = "Chhanni - " + projectName + " - " + stamp;
        CH.main = chGetBin(app.project.rootItem, name);
        if (!CH.main) return chErr("bin create failed");
        return chOk('"bin":"' + chEsc(name) + '"');
    } catch (e) { return chErr(e); }
}

/* 2. import a chunk of files into a sub-bin */
function chhanniImport(binName, paths) {
    try {
        var b = CH.bins[binName] || (CH.bins[binName] = chGetBin(CH.main, binName));
        var fresh = [];
        for (var i = 0; i < paths.length; i++) if (!CH.items[chNorm(paths[i])]) fresh.push(paths[i]);
        if (fresh.length) app.project.importFiles(fresh, true, b, false);
        chCollect(b);
        var found = 0;
        for (var j = 0; j < paths.length; j++) if (CH.items[chNorm(paths[j])]) found++;
        return chOk('"found":' + found + ',"asked":' + paths.length);
    } catch (e) { return chErr(e); }
}

/* 3. labels, select markers and slow-motion for a chunk of clips */
function chhanniMark(clips, labelIdx, slow) {
    var n = 0, slowed = 0;
    try {
        for (var i = 0; i < clips.length; i++) {
            var c = clips[i], pi = CH.items[chNorm(c.path)];
            if (!pi) continue;
            n++;
            try { pi.setColorLabel(labelIdx[c.label]); } catch (e1) {}
            if (slow && c.conform && !CH.over[chNorm(c.path)]) {
                try { pi.setOverrideFrameRate(c.conform); CH.over[chNorm(c.path)] = c.conform; slowed++; } catch (e2) {}
            }
            var k = CH.over[chNorm(c.path)] ? c.fps / CH.over[chNorm(c.path)] : 1;
            try {
                var ms = pi.getMarkers();
                for (var s = 0; s < c.selects.length; s++) {
                    var x = c.selects[s];
                    var m = ms.createMarker(x.start * k);
                    m.name = (x.star ? "★ " : "") + "Select " + (s + 1) + " · " + x.score;
                    m.comments = "Chhanni · shake " + x.shake + " · sharp " + x.sharp;
                    m.end = x.end * k;
                    try { m.setColorByIndex(x.star ? 4 : 5); } catch (e3) {}
                }
            } catch (e4) {}
        }
        return chOk('"marked":' + n + ',"slowed":' + slowed);
    } catch (e) { return chErr(e); }
}

/* 4. build one sequence */
function chhanniSequence(seq) {
    try {
        var first = null, i;
        for (i = 0; i < seq.items.length && !first; i++) first = CH.items[chNorm(seq.items[i].path)];
        if (!first) return chErr("no clips for " + seq.name);
        var sq = app.project.createNewSequenceFromClips(seq.name, [first], CH.main);
        if (!sq) return chErr("sequence create failed: " + seq.name);
        // remove the full clip Premiere put in, keep the matched settings
        var t, k;
        for (t = 0; t < sq.videoTracks.numTracks; t++)
            for (k = sq.videoTracks[t].clips.numItems - 1; k >= 0; k--) sq.videoTracks[t].clips[k].remove(false, false);
        for (t = 0; t < sq.audioTracks.numTracks; t++)
            for (k = sq.audioTracks[t].clips.numItems - 1; k >= 0; k--) sq.audioTracks[t].clips[k].remove(false, false);
        var vt = sq.videoTracks[0];
        var pos = 0, placed = 0, used = [];
        for (i = 0; i < seq.items.length; i++) {
            var it = seq.items[i], pi = CH.items[chNorm(it.path)];
            if (!pi) continue;
            var sc = CH.over[chNorm(it.path)] ? it.fps / CH.over[chNorm(it.path)] : 1;
            pi.setInPoint(chTicks(it.start * sc), 4);
            pi.setOutPoint(chTicks(it.end * sc), 4);
            var tm = new Time(); tm.ticks = chTicks(pos);
            vt.overwriteClip(pi, tm);
            used.push(pi);
            var n = vt.clips.numItems;
            if (n > 0) {
                var last = vt.clips[n - 1];
                pos = last.end.seconds;
                if (it.star) { try { var sm = sq.markers.createMarker(last.start.seconds); sm.name = "★ Favourite"; sm.comments = it.name; } catch (e5) {} }
            } else {
                pos += (it.end - it.start) * sc;
            }
            placed++;
        }
        for (i = 0; i < used.length; i++) { try { used[i].clearInPoint(); used[i].clearOutPoint(); } catch (e6) {} }
        return chOk('"name":"' + chEsc(seq.name) + '","placed":' + placed);
    } catch (e) { return chErr(e); }
}

/* ---------------- v3: multi-track versions ---------------- */
function chFindItem(path) { return CH.items[chNorm(path)] || null; }

function chhanniVersionStart(binName, paths) {
    try {
        CH.items = CH.items || {};
        CH.vbin = chGetBin(app.project.rootItem, binName);
        var fresh = [];
        chCollect(CH.vbin);
        for (var i = 0; i < paths.length; i++) if (!CH.items[chNorm(paths[i])]) fresh.push(paths[i]);
        if (fresh.length) app.project.importFiles(fresh, true, CH.vbin, false);
        chCollect(CH.vbin);
        var found = 0;
        for (var j = 0; j < paths.length; j++) if (CH.items[chNorm(paths[j])]) found++;
        return chOk('"found":' + found + ',"asked":' + paths.length);
    } catch (e) { return chErr(e); }
}

function chRemoveLinkedAudio(sq, pi, startSec) {
    for (var t = 0; t < sq.audioTracks.numTracks; t++) {
        var cl = sq.audioTracks[t].clips;
        for (var k = cl.numItems - 1; k >= 0; k--) {
            try {
                if (Math.abs(cl[k].start.seconds - startSec) < 0.05 && cl[k].projectItem && cl[k].projectItem.nodeId === pi.nodeId) cl[k].remove(false, false);
            } catch (e) {}
        }
    }
}

function chhanniVersion(v, slowFps) {
    try {
        var firstV = null, i, it;
        for (i = 0; i < v.items.length && !firstV; i++) if (v.items[i].media !== "a") firstV = chFindItem(v.items[i].path);
        if (!firstV) return chErr("no video items");
        var talking = {};
        for (i = 0; i < v.items.length; i++) if (v.items[i].media !== "v") talking[chNorm(v.items[i].path)] = true;
        for (i = 0; i < v.items.length; i++) {
            it = v.items[i];
            var p0 = chFindItem(it.path);
            if (p0 && slowFps && !talking[chNorm(it.path)] && it.fps && it.fps > slowFps * 1.9 && !CH.over[chNorm(it.path)]) {
                try { p0.setOverrideFrameRate(slowFps); CH.over[chNorm(it.path)] = slowFps; } catch (e0) {}
            }
        }
        if (v.replace) {
            for (var q = app.project.sequences.numSequences - 1; q >= 0; q--) {
                try { if (app.project.sequences[q].name === v.name) app.project.deleteSequence(app.project.sequences[q]); } catch (eD) {}
            }
        }
        var sq = app.project.createNewSequenceFromClips(v.name, [firstV], CH.vbin);
        if (!sq) return chErr("sequence create failed");
        var t, k;
        for (t = 0; t < sq.videoTracks.numTracks; t++) for (k = sq.videoTracks[t].clips.numItems - 1; k >= 0; k--) sq.videoTracks[t].clips[k].remove(false, false);
        for (t = 0; t < sq.audioTracks.numTracks; t++) for (k = sq.audioTracks[t].clips.numItems - 1; k >= 0; k--) sq.audioTracks[t].clips[k].remove(false, false);
        var placed = 0, skipped = 0, used = [];
        for (i = 0; i < v.items.length; i++) {
            it = v.items[i];
            var pi = chFindItem(it.path);
            if (!pi) { skipped++; continue; }
            var sc = (CH.over[chNorm(it.path)] && it.fps) ? it.fps / CH.over[chNorm(it.path)] : 1;
            pi.setInPoint(chTicks(it.start * sc), 4);
            pi.setOutPoint(chTicks(it.end * sc), 4);
            var tm = new Time(); tm.ticks = chTicks(it.rec);
            var idx = parseInt(it.track.substring(1), 10) - 1;
            if (it.media === "a") {
                var at = idx < sq.audioTracks.numTracks ? sq.audioTracks[Math.max(idx, (v.hasAV ? 1 : 0))] : sq.audioTracks[sq.audioTracks.numTracks - 1];
                at.overwriteClip(pi, tm);
            } else {
                var vt = idx < sq.videoTracks.numTracks ? sq.videoTracks[idx] : sq.videoTracks[sq.videoTracks.numTracks - 1];
                vt.overwriteClip(pi, tm);
                if (it.media === "v") chRemoveLinkedAudio(sq, pi, it.rec);
                if (it.zoom) {
                    try {
                        var last = vt.clips[vt.clips.numItems - 1];
                        for (var c = 0; c < last.components.numItems; c++) {
                            var comp = last.components[c];
                            if (comp.displayName === "Motion" || comp.matchName === "AE.ADBE Motion") {
                                for (var pp = 0; pp < comp.properties.numItems; pp++) {
                                    if (comp.properties[pp].displayName === "Scale") comp.properties[pp].setValue(it.zoom * 100, true);
                                }
                            }
                        }
                    } catch (eZ) {}
                }
            }
            used.push(pi);
            placed++;
        }
        for (i = 0; i < v.markers.length; i++) {
            try { var m = sq.markers.createMarker(v.markers[i].rec); m.name = v.markers[i].name; m.comments = v.markers[i].note; } catch (e7) {}
        }
        for (i = 0; i < used.length; i++) { try { used[i].clearInPoint(); used[i].clearOutPoint(); } catch (e6) {} }
        var caps = "no";
        if (v.srt) {
            try {
                app.project.importFiles([v.srt], true, CH.vbin, false);
                var srtItem = null;
                for (var s2 = 0; s2 < CH.vbin.children.numItems; s2++) {
                    var ch2 = CH.vbin.children[s2];
                    try { if (ch2.getMediaPath && chNorm(ch2.getMediaPath()) === chNorm(v.srt)) srtItem = ch2; } catch (eS) {}
                }
                if (srtItem && sq.createCaptionTrack) { sq.createCaptionTrack(srtItem, 0); caps = "yes"; }
            } catch (eC) { caps = "err"; }
        }
        return chOk('"name":"' + chEsc(v.name) + '","placed":' + placed + ',"skipped":' + skipped + ',"captions":"' + caps + '"');
    } catch (e) { return chErr(e); }
}

/* smart bins: master clips into category bins + subclips of selects into collection bins */
function chhanniSmartBins(rootName, cats, subs, labelIdx) {
    try {
        var root = chGetBin(app.project.rootItem, rootName), n = 0, ns = 0, i;
        for (i = 0; i < cats.length; i++) {
            var cb = chGetBin(root, cats[i].bin);
            var pi = CH.items[chNorm(cats[i].path)];
            if (!pi) { app.project.importFiles([cats[i].path], true, cb, false); chCollect(cb); pi = CH.items[chNorm(cats[i].path)]; }
            else { try { pi.moveBin(cb); } catch (e1) {} }
            if (pi) { n++; try { pi.setColorLabel(labelIdx[cats[i].label]); } catch (e2) {} }
        }
        for (i = 0; i < subs.length; i++) {
            var s = subs[i], src = CH.items[chNorm(s.path)];
            if (!src) continue;
            var sb = chGetBin(root, s.bin);
            try {
                var sc = src.createSubClip(s.name, chTicks(s.start), chTicks(s.end), 0, 1, 1);
                if (sc) { sc.moveBin(sb); ns++; }
            } catch (e3) {}
        }
        return chOk('"clips":' + n + ',"subclips":' + ns);
    } catch (e) { return chErr(e); }
}
