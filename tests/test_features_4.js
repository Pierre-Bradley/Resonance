#!/usr/bin/env node
// Harnais d'execution du script principal de index.html dans un contexte vm
// avec DOM simulate minimal. Couvre les jalons 4.1-4.3 (session precedente)
// et 4.4-4.7 : raccourci V, radio, fondu enchaine, visualiseur, import
// playlist, qualite, sauvegarde.
'use strict';
const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync('/home/z/my-project/download/templates/index.html', 'utf-8');
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error('script introuvable'); process.exit(1); }
const code = m[1]
    // instrumentation : les erreurs avalees par recordListen deviennent visibles
    .replace('.catch(() => { /* statistique perdue : sans consequence */ });',
             ".catch(e => console.log('RECERR:', e && e.stack));");

// ---------------------------------------------------------------- DOM mock
const els = {};
const docListeners = {};
const audioListeners = {};   // audio-player : type -> dernier callback
let seq = 0;

function makeEl(id) {
    const el = {
        id, style: { setProperty() {}, getPropertyValue() { return ''; } }, children: [], _classes: new Set(),
        classList: {
            add: (...c) => c.forEach(x => el._classes.add(x)),
            remove: (...c) => c.forEach(x => el._classes.delete(x)),
            toggle: (c, on) => {
                if (on === undefined) el._classes.has(c) ? el._classes.delete(c) : el._classes.add(c);
                else on ? el._classes.add(c) : el._classes.delete(c);
            },
            contains: c => el._classes.has(c)
        },
        innerHTML: '', textContent: '', value: '1', disabled: false, paused: true,
        duration: 200, currentTime: 0, volume: 1, playbackRate: 1, src: '',
        clientWidth: 300, clientHeight: 60,
        addEventListener(type, cb) { if (id === 'audio-player') audioListeners[type] = cb; },
        removeEventListener() {}, setAttribute() {}, getAttribute() { return null; },
        appendChild(ch) { this.children.push(ch); },
        querySelector(sel) {
            const key = id + '::' + sel;
            if (!els[key]) els[key] = makeEl(key);
            return els[key];
        },
        querySelectorAll() { return []; }, focus() {}, load() {}, click() {}, remove() {},
        play() { this.paused = false; calls.play.push(this.id); return Promise.resolve(); },
        pause() { this.paused = true; calls.pause.push(this.id); },
        getContext(type) {
            if (type !== '2d') return null;
            if (!this._c2d) {
                this._c2d = {
                    clearRect() { drawStats.clearRect++; },
                    fillRect() { drawStats.fillRect++; },
                    beginPath() { drawStats.beginPath++; },
                    moveTo() {}, lineTo() {},
                    stroke() { drawStats.stroke++; },
                    createLinearGradient() { return { addColorStop() {} }; },
                    fillStyle: '', strokeStyle: '', lineWidth: 1, lineJoin: ''
                };
            }
            return this._c2d;
        },
        getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 20 }; },
        scrollTop: 0, clientTop: 0, scrollHeight: 5000,
        offsetHeight: 0, files: [], dataset: {}
    };
    return el;
}

const calls = { play: [], pause: [], fetch: [], setAction: [], metadata: [], storage: [] };
const drawStats = { fillRect: 0, clearRect: 0, beginPath: 0, stroke: 0 };
const storageMap = new Map();

const audio = () => els['audio-player'];

const documentMock = {
    getElementById: id => els[id] || (els[id] = makeEl(id)),
    createElement: () => makeEl('created-' + (++seq)),
    querySelectorAll: () => [], querySelector: () => null,
    addEventListener: (type, cb) => { docListeners[type] = cb; },
    removeEventListener() {}, body: makeEl('body'),
    documentElement: { setAttribute() {}, getAttribute() { return null; } }
};

// Horloge et requestAnimationFrame pilotables : le fondu enchaine et le
// visualiseur n'avancent que quand on vide la file de frames.
const perfClock = { t: 1000 };
const rafQueue = [];
function flushRaf(rounds = 1) {
    for (let r = 0; r < rounds; r++) {
        const q = rafQueue.splice(0);
        q.forEach(cb => cb(perfClock.t));
    }
}

// Web Audio factice : suffisant pour initEqualizer + AnalyserNode greffe.
class FakeBiquad { constructor() { this.type = ''; this.frequency = { value: 0 }; this.gain = { value: 0 }; this.Q = { value: 0 }; } connect() {} disconnect() {} }
class FakeAnalyser {
    constructor() { this.fftSize = 0; this.smoothingTimeConstant = 0; this.frequencyBinCount = 128; }
    connect() {}
    getByteFrequencyData(arr) { for (let i = 0; i < arr.length; i++) arr[i] = 100 + (i % 60); }
}
class FakeMESource { constructor(el) { this.el = el; } connect() { return this; } }
class FakeAudioContext {
    constructor() { this.state = 'running'; this.destination = {}; }
    resume() { return Promise.resolve(); }
    createBiquadFilter() { return new FakeBiquad(); }
    createAnalyser() { return new FakeAnalyser(); }
    createMediaElementSource(el) { return new FakeMESource(el); }
}

const mediaHandlers = {};
const sandbox = {
    console,
    document: documentMock,
    window: {
        addEventListener() {}, matchMedia: () => ({ matches: false }),
        location: { href: 'http://resonance.local/' }, devicePixelRatio: 1,
        AudioContext: FakeAudioContext
    },
    navigator: {
        mediaSession: {
            setActionHandler: (a, fn) => { mediaHandlers[a] = fn; calls.setAction.push(a); },
            setPositionState: () => {}
        }
    },
    localStorage: {
        getItem: k => (storageMap.has(k) ? storageMap.get(k) : null),
        setItem: (k, v) => { storageMap.set(k, v); calls.storage.push(['set', k, v]); },
        removeItem: k => { storageMap.delete(k); calls.storage.push(['rm', k]); }
    },
    fetch: (url, opts) => {
        calls.fetch.push({ url, opts });
        const jsonFor = () => {
            if (url === '/api/state') {
                return {
                    songs: [
                        { name: 'X - A.wav', path: 'a.wav', title: 'A', artist: 'X', album: 'Al', genre: 'G', has_cover: true, mtime: 1 },
                        { name: 'X - A2.wav', path: 'a2.wav', title: 'A2', artist: 'X', album: 'Al', genre: 'G', has_cover: false, mtime: 2 },
                        { name: 'Y - C.wav', path: 'c.wav', title: 'C', artist: 'Y', album: 'Other', genre: 'G', has_cover: false, mtime: 3 }
                    ],
                    favorites: [], playlists: {}, play_counts: {}
                };
            }
            if (url.startsWith('/api/search')) {
                // pagination (scroll infini) : 40 resultats par page, 5 sur la 3e
                const m = url.match(/offset=(\d+)/);
                const off = m ? parseInt(m[1], 10) : 0;
                const n = off >= 80 ? 5 : 40;
                return { results: Array.from({ length: n }, (_, i) => ({
                    id: 'yt' + (off + i), title: 'T' + (off + i), artist: 'X', artwork: '', source: 'youtube'
                })) };
            }
            if (url === '/api/backup') return { app: 'RESONANCE', favorites: [], playlists: {}, play_counts: {} };
            if (url === '/api/restore') return { success: true, favorites: 1, playlists: 0, play_counts: 0 };
            return { success: true, path: 'a.wav', plays: 1 };
        };
        return Promise.resolve({ ok: true, status: 200, headers: { get: () => null }, json: async () => jsonFor() });
    },
    MediaMetadata: class { constructor(d) { calls.metadata.push(d); } },
    Audio: class { constructor() { return makeEl('audio-el-' + (++seq)); } },
    Blob: class { constructor(parts) { this.text = () => Promise.resolve(parts.join('')); } },
    // URL maison : createObjectURL de Node verifierait un Blob interne,
    // incompatible avec le Blob factice du contexte.
    URL: class extends URL {
        static createObjectURL() { return 'blob:mock'; }
        static revokeObjectURL() {}
    },
    setInterval: () => 0, clearInterval: () => {}, setTimeout: () => 0, clearTimeout: () => {},
    requestAnimationFrame: cb => { rafQueue.push(cb); return rafQueue.length; },
    cancelAnimationFrame: () => {},
    performance: { now: () => perfClock.t },
    Date, Promise, JSON, Math, isFinite, parseFloat, parseInt, isNaN, Array, Object
};
URL.createObjectURL = URL.createObjectURL || (() => 'blob:mock');
URL.revokeObjectURL = URL.revokeObjectURL || (() => {});
vm.createContext(sandbox);
vm.runInContext(code, sandbox, { filename: 'index.html<script>' });

const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
function check(name, cond) {
    console.log((cond ? '  OK  ' : ' FAIL ') + name);
    if (!cond) failures++;
}
const probe = expr => vm.runInContext(expr, sandbox);

(async () => {
    await sleep(30);   // laisse fetchData(true) peupler state
    const g = sandbox;

    // ---------------- 4.1 Media Session (rappel)
    check('4.1 handlers casque/verrou enregistres',
        ['play', 'pause', 'previoustrack', 'nexttrack', 'seekto'].every(a => mediaHandlers[a]));
    g.updateMediaSessionForLocal('a.wav', 'Titre A', 'Artiste X');
    const md = calls.metadata[calls.metadata.length - 1];
    check('4.1 metadonnees + pochette absolue', md.title === 'Titre A' && md.album === 'Al'
        && md.artwork[0].src === 'http://resonance.local/api/cover/a.wav');

    // ---------------- raccourci V : minuteur de veille
    docListeners['keydown']({ code: 'KeyV', key: 'v', target: { tagName: 'BODY' }, preventDefault() {} });
    check('raccourci V ouvre le minuteur de veille', els['sleep-modal'].style.display === 'flex');
    els['sleep-modal'].style.display = 'none';

    // ---------------- 4.2 minuteur (fondu, arret) — rappel rapide
    const realNow = Date.now;
    const fake = { t: realNow.call(Date) };
    Date.now = () => fake.t;
    g.armSleepTimer(1);
    fake.t += 45 * 1000; g.sleepSecondTick();
    const midFade = audio().volume;
    fake.t += 25 * 1000; g.sleepSecondTick();
    check('4.2 fondu puis arret propre', midFade < 1 && audio().paused === true && Math.abs(audio().volume - 1) < 0.001);
    Date.now = realNow;

    // ---------------- 4.3 statistiques (rappel)
    g.recordListen('a.wav');
    await sleep(20);
    check('4.3 POST /api/play_count', calls.fetch.some(f => f.url === '/api/play_count'));
    const row = g.createSongRow({ path: 'a.wav', title: 'A', artist: 'X', album: 'Al', has_cover: false }, 0);
    check('4.3 compteur discret', row.innerHTML.includes('play-count'));

    // ---------------- 4.4b radio locale : file continue meme artiste
    audio().paused = false;
    probe("startRadioFromSong(state.songs[0])");
    await sleep(10);
    flushRaf(1);
    check('4.4 radio : file = graine + titres du meme artiste',
        probe('queue.length') === 2 && probe('!!radioMode') === true);
    check('4.4 radio : bandeau visible dans le panneau',
        !els['radio-strip'].classList.contains('hidden'));
    check('4.4 radio : lecture partie sur la graine', audio().src.includes('a.wav'));

    // ---------------- 4.4b epuisement local -> completion en ligne
    probe('queueCurrentIndex = queue.length - 1; audioPlayer.pause();');
    calls.fetch.length = 0;
    g.playNext();
    await sleep(40);
    check('4.4 radio : recherche en ligne effectuee',
        calls.fetch.some(f => f.url.startsWith('/api/search_youtube?q=X')));
    check('4.4 radio : titres en ligne ajoutes et lecture reprise',
        probe('queue.length') === 14 && probe('queueCurrentIndex') === 2);
    check('4.4 radio : la piste suivante (en ligne) se joue', calls.play.length > 0);

    g.stopRadio();
    check('4.4 radio : extinction propre', probe('!radioMode') === true
        && els['radio-strip'].classList.contains('hidden'));

    // ---------------- 4.5b fondu enchaine : cycle complet
    probe("queue.length = 0; queue.push({path: 'a.wav', title: 'A', artist: 'X'}, {path: 'a2.wav', title: 'A2', artist: 'X'}); queueCurrentIndex = 0;");
    probe("currentPlayingPath = 'a.wav'; currentOnlineId = null; displayedSongs = [];");
    audio().paused = false;
    audio().src = '/stream/a.wav';
    audio().duration = 200;
    audio().currentTime = 199;
    // touche B : le fondu passe de off a 2 s (le bouton a ete retire de la barre)
    docListeners['keydown']({ code: 'KeyB', key: 'b', target: { tagName: 'BODY' }, preventDefault() {} });
    check('4.5 raccourci B : fondu off -> 2 s', probe('crossSecs') === 2);
    g.maybeStartCrossfade();
    check('4.5 fondu : seconde source prechargee', probe('crossfading') === true
        && probe('crossPlayer.src').includes('/stream/a2.wav')
        && probe('crossPlayer.volume') === 0);
    perfClock.t += 1000;   // milieu du croisement
    flushRaf(2);
    check('4.5 fondu : gains croises au milieu', audio().volume < 1 && probe('crossPlayer.volume') > 0
        && Math.abs(audio().volume + probe('crossPlayer.volume') - 1) < 0.05);
    perfClock.t += 1100;   // fondu termine
    flushRaf(3);
    await sleep(30);
    check('4.5 fondu : bascule sur le titre suivant via audioPlayer',
        probe('!crossfading') === true && audio().src.includes('a2.wav')
        && probe('currentPlayingPath') === 'a2.wav');
    check('4.5 fondu : statistique du titre fini enregistree',
        calls.fetch.some(f => f.url === '/api/play_count' && f.opts && String(f.opts.body).includes('a.wav')));
    check('4.5 fondu : cycle B continue 2 -> 4 s', (g.toggleCrossfade(), probe('crossSecs') === 4));

    // ---------------- 4.5a visualiseur (actif par defaut + touche W)
    check('4.5 visualiseur : ACTIF PAR DEFAUT (prefs vierges)', probe('vizWanted') === true);
    drawStats.fillRect = 0;
    // le contexte audio ne nait qu'au premier geste : pointerdown arme le dessin
    if (docListeners['pointerdown']) docListeners['pointerdown']({ target: documentMock.body });
    audio().paused = false;
    flushRaf(4);
    check('4.5 visualiseur : arme au premier geste, dessine des barres',
        !els['viz-canvas'].classList.contains('hidden') && drawStats.fillRect > 20);
    // touche W : desactive puis reactive
    docListeners['keydown']({ code: 'KeyW', key: 'w', target: { tagName: 'BODY' }, preventDefault() {} });
    check('4.5 touche W : coupe le visualiseur',
        els['viz-canvas'].classList.contains('hidden') && probe('!vizActive') === true);
    docListeners['keydown']({ code: 'KeyW', key: 'w', target: { tagName: 'BODY' }, preventDefault() {} });
    check('4.5 touche W : reactive le visualiseur',
        !els['viz-canvas'].classList.contains('hidden') && probe('vizActive') === true);

    // ---------------- refonte d'en-tete : tri fleche, menu regroupe, pouls
    probe("toggleSortDir(); toggleSortDir();");   // cree les elements + revient a A-Z
    g.toggleSortDir();
    check('UI : fleche de tri bascule A-Z -> Z-A', probe("currentSort") === 'za'
        && els['sort-dir-icon'].className === 'fas fa-arrow-up-a-z');
    g.toggleSortDir();
    check('UI : fleche de tri revient a A-Z', probe("currentSort") === 'az'
        && els['sort-dir-icon'].className === 'fas fa-arrow-down-a-z');

    sandbox.document.getElementById('header-menu');   // force la creation du mock
    check('UI : menu d\u2019entete manipulable', (() => {
        sandbox.document.getElementById('header-menu');
        g.toggleHeaderMenu();   // mock sans classes initiales : 1er toggle -> ferme
        g.toggleHeaderMenu();   // 2e toggle -> ouvert
        return !els['header-menu'].classList.contains('hidden');
    })());
    check('UI : menu contient Telechargements, Stockage, Sauvegarde',
        html.includes('Telechargements en cours') && html.includes('Stockage hors ligne')
        && html.includes('Sauvegarde / restauration') && html.includes('header-menu-item'));
    g.menuOpenDl();
    check('UI : ligne Telechargements ouvre le panneau + referme le menu',
        !els['dl-panel'].classList.contains('hidden')
        && els['header-menu'].classList.contains('hidden'));

    drawStats.stroke = 0;
    perfClock.t += 40;   // la boucle du pouls saute les frames < 33 ms
    flushRaf(2);
    check('UI : pouls du serveur dessine (ligne de vie EKG)',
        drawStats.stroke > 0 && probe('typeof ekgShape') === 'function');
    vm.runInContext("applyNetStatus({ online: false, audius_ok: false, youtube_ok: false }, true)", sandbox);
    drawStats.stroke = 0;
    perfClock.t += 40;
    flushRaf(2);
    check('UI : pouls continue de vivre en flatline (hors ligne)', drawStats.stroke > 0);
    // retour a l'etat en ligne pour la suite des tests (radio, scroll infini)
    vm.runInContext("applyNetStatus({ online: true, audius_ok: true, youtube_ok: true }, true)", sandbox);

    // ---------------- scroll infini : pages de 40, fin propre
    probe("currentView = 'online'; onlineSource = 'youtube'; onlineSearched = true; onlineQuery = 'X'; onlineFetched = 40; onlineHasMore = true; onlinePageLoading = false; onlineResults = [];");
    probe("onlineResults.push(...Array.from({length: 40}, (_, i) => ({ id: 'yt' + i, title: 'T' + i, artist: 'X', artwork: '', source: 'youtube' })));\n" +
          "onlineReqId++;");
    await g.loadMoreOnline();
    check('scroll infini : 2e page chargee (80 resultats)', probe('onlineResults.length') === 80
        && probe('onlineFetched') === 80 && probe('onlineHasMore') === true);
    await g.loadMoreOnline();
    check('scroll infini : 3e page partielle puis fin de source',
        probe('onlineResults.length') === 85 && probe('onlineHasMore') === false);

    // ---------------- 4.6a import playlist : detection + rendu
    check('4.6 detection lien playlist', g.isPlaylistUrl('https://www.youtube.com/playlist?list=PLxyz') === true
        && g.isPlaylistUrl('https://youtu.be/abc') === false
        && g.isPlaylistUrl('titre musique') === false);
    // le 3e titre ('A' de 'X') existe deja en bibliotheque : decoche d'office
    probe("playlistImportItems = [{id:'v1', title:'Un', artist:'A', duration:200, artwork:'', checked:false}," +
          " {id:'v2', title:'Deux', artist:'A', duration:0, artwork:'', checked:false}," +
          " {id:'v3', title:'A', artist:'X', duration:180, artwork:'', checked:false}];");
    g.renderPlaylistImportList();
    check('4.6 liste a cocher rendue + compte (doublon deja present decoche)',
        els['playlist-import-list'].children.length === 3
        && els['playlist-count'].textContent === '2 / 3 coches');
    els['dl-quality'].value = '320';
    calls.fetch.length = 0;
    await g.importPlaylistSelection();
    const plPost = calls.fetch.find(f => f.url === '/api/download_youtube');
    const plBodies = calls.fetch.filter(f => f.url === '/api/download_youtube').map(f => JSON.parse(f.opts.body));
    check('4.6 import : une tache par titre coche (doublon exclu, qualite comprise)',
        plBodies.length === 2 && plBodies.some(b => b.id === 'v1')
        && !plBodies.some(b => b.id === 'v3') && plBodies[0].quality === '320');

    // ---------------- 4.6b qualite memorisee
    g.saveDlQuality();
    check('4.6 qualite persistee', storageMap.get('resonance-dl-quality') === '320'
        && g.readDlQuality() === '320');

    // ---------------- 4.7a sauvegarde / restauration
    await g.exportBackup();
    check('4.7 export JSON telecharge', calls.fetch.some(f => f.url === '/api/backup')
        && els['import-toast-msg'].textContent.includes('Sauvegarde'));
    const fakeInput = {
        value: '',
        files: [{ text: async () => JSON.stringify({ app: 'RESONANCE', favorites: ['z.wav'], playlists: {}, play_counts: {} }) }]
    };
    await g.importBackupFile(fakeInput);
    check('4.7 import JSON restaure', calls.fetch.some(f => f.url === '/api/restore')
        && els['import-toast-msg'].textContent.includes('restaurees'));

    console.log(failures === 0 ? '\nTOUS LES TESTS PASSENT' : `\n${failures} ECHEC(S)`);
    process.exit(failures === 0 ? 0 : 1);
})().catch(e => { console.error('ERREUR harnais :', e); process.exit(1); });
