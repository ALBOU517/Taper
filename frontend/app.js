import { createApp, reactive, ref, computed, watch, onMounted, onUnmounted } from "./vendor/vue.esm-browser.prod.js";

/* ================================================================ utilities */
const store = reactive({
  token: localStorage.getItem("taper.token") || "",
  user: null,
  toast: "",
});

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json", ...(store.token ? { Authorization: `Bearer ${store.token}` } : {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401 && store.token) { logout(); throw new Error("Your session expired. Sign in again."); }
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    const d = data?.detail;
    throw new Error(Array.isArray(d) ? d.map((x) => `${x.loc?.at(-1)}: ${x.msg}`).join("; ") : d || `Request failed (${res.status})`);
  }
  return data;
}

function setAuth({ token, user }) {
  store.token = token; store.user = user;
  localStorage.setItem("taper.token", token);
}
function logout() {
  store.token = ""; store.user = null;
  localStorage.removeItem("taper.token");
  go("/welcome");
}
let toastTimer;
function toast(msg) {
  store.toast = msg; clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (store.toast = ""), 4200);
}

/* ================================================================ theme */
const THEME_KEY = "taper.theme";
const theme = reactive({ choice: localStorage.getItem(THEME_KEY) || "system", resolved: "light" });
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
function applyTheme() {
  theme.resolved = theme.choice === "system" ? (darkQuery.matches ? "dark" : "light") : theme.choice;
  document.documentElement.dataset.theme = theme.resolved;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme.resolved === "dark" ? "#0f141a" : "#edf0ee");
}
function setTheme(choice) { theme.choice = choice; localStorage.setItem(THEME_KEY, choice); applyTheme(); }
darkQuery.addEventListener("change", () => theme.choice === "system" && applyTheme());
applyTheme();

const iso = (d) => { const x = new Date(d); return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`; };
const parseISO = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const mondayOf = (d) => { const x = new Date(d); x.setHours(0, 0, 0, 0); return addDays(x, -((x.getDay() + 6) % 7)); };
const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const fmtDay = (d, opts = { weekday: "long", month: "long", day: "numeric" }) => parseISO(typeof d === "string" ? d : iso(d)).toLocaleDateString(undefined, opts);
const fmtMin = (m) => { m = Math.round(m || 0); if (m < 60) return `${m} min`; const h = Math.floor(m / 60), r = m % 60; return r ? `${h} h ${String(r).padStart(2, "0")}` : `${h} h`; };
const fmtDist = (m, sport) => !m ? "" : sport === "swim" ? `${Math.round(m)} m` : `${(m / 1000).toFixed(1)} km`;
const SPORTS = { run: "Run", bike: "Bike", swim: "Swim", gym: "Strength", walk: "Walk", mobility: "Mobility" };
const STATUS_LABEL = { productive: "Building", neutral: "Balanced", fresh: "Fresh", risk: "Overreaching", detraining: "Detraining" };

/* ================================================================ router */
const route = reactive({ path: "/today", query: {} });
function readHash() {
  const h = location.hash.slice(1) || "/today";
  const [p, q] = h.split("?");
  route.path = p; route.query = Object.fromEntries(new URLSearchParams(q || ""));
}
function go(path) { location.hash = path; }
window.addEventListener("hashchange", readHash);
readHash();

/* ================================================================ workout pieces */
const qty = (s) => {
  if (s.distance_m) return s.distance_m >= 1000 ? `${+(s.distance_m / 1000).toFixed(2)} km` : `${Math.round(s.distance_m)} m`;
  if (s.duration_s >= 60) return `${+(s.duration_s / 60).toFixed(1)}′`;
  return `${Math.round(s.duration_s || 0)}″`;
};
const tgtLabel = (t) => {
  if (!t) return "";
  if (t.kind === "zone") return `Z${t.value}`;
  if (t.kind === "pace") { const m = Math.floor(t.value / 60), s = Math.round(t.value % 60); return `${m}:${String(s).padStart(2, "0")}/${t.unit}`; }
  if (t.kind === "ftp_pct") return `${t.value}% FTP`;
  if (t.kind === "power") return `${t.value} W`;
  return `RPE ${t.value}`;
};
const tgtClass = (t) => {
  if (!t) return "";
  let z = 0;
  if (t.kind === "zone") z = t.value;
  else if (t.kind === "ftp_pct") z = t.value < 75 ? 2 : t.value < 90 ? 3 : t.value < 105 ? 4 : 5;
  else if (t.kind === "rpe") z = t.value <= 4 ? 2 : t.value <= 6 ? 3 : t.value <= 8 ? 4 : 5;
  return z ? `z${z}` : "";
};
const stepName = (s) => s.label || ({ rest: "Rest", warmup: "Warm-up", cooldown: "Cool-down", easy: "Easy", work: "Work" }[s.role] || s.role);

const WorkoutSteps = {
  props: { steps: Array },
  setup: () => ({ qty, tgtLabel, tgtClass, stepName }),
  template: `
  <ul class="steps">
    <li v-for="(n, i) in steps" :key="i">
      <template v-if="n.type === 'step'">
        <span class="q">{{ qty(n) }}</span><span>{{ stepName(n) }}</span>
        <span v-if="n.target" class="tgt" :class="tgtClass(n.target)">{{ tgtLabel(n.target) }}</span><span v-else></span>
      </template>
      <template v-else-if="n.type === 'repeat'">
        <span class="rep">{{ n.count }}×</span>
        <ul class="inner">
          <li v-for="(c, j) in n.steps" :key="j">
            <span><span class="num">{{ qty(c) }}</span> {{ stepName(c) }}</span>
            <span v-if="c.target" class="tgt" :class="tgtClass(c.target)">{{ tgtLabel(c.target) }}</span>
          </li>
        </ul><span></span>
      </template>
      <template v-else-if="n.type === 'exercise'">
        <span class="q">{{ n.sets }}×{{ n.reps }}</span><span>{{ n.name }}</span>
        <span v-if="n.rpe" class="tgt" :class="tgtClass({kind:'rpe', value:n.rpe})">RPE {{ n.rpe }}</span>
        <span v-else-if="n.load_kg" class="tgt">{{ n.load_kg }} kg</span><span v-else></span>
      </template>
      <template v-else-if="n.type === 'superset'">
        <span class="rep">SS</span>
        <ul class="inner"><li v-for="(e, j) in n.exercises" :key="j">
          <span><span class="num">{{ e.sets }}×{{ e.reps }}</span> {{ e.name }}</span>
          <span v-if="e.rpe" class="tgt">RPE {{ e.rpe }}</span></li></ul><span></span>
      </template>
    </li>
  </ul>`,
};

const Profile = {
  props: { profile: Array },
  template: `<div class="profile" aria-hidden="true">
    <span v-for="(p, i) in profile" :key="i" :class="{ rest: p[1] < 0.5 }"
      :style="{ flexGrow: p[0], height: Math.max(8, Math.min(100, p[1] / 1.15 * 100)) + '%' }"></span></div>`,
};

const CHECK_HELP = {
  run: "Run 20 minutes at a steady effort where you could still say short sentences (RPE 6/10). Walk whenever you need to. Afterwards, note how many minutes you ran before the first walk break and the distance.",
  swim: "Swim up to 400 m at an easy-steady pace. Stop whenever you need to. Note the distance before your first stop and the total time.",
  bike: "Ride 30 minutes on flat roads or a trainer at a steady effort. Note the distance and whether you finished comfortably.",
};

const WorkoutCard = {
  components: { WorkoutSteps, Profile },   // FuelPlan is registered globally (defined later)
  props: { s: Object, showDay: Boolean, fuel: Array },
  emits: ["changed"],
  setup(props, { emit }) {
    const logging = ref(false);
    const busy = ref(false);
    const changes = ref(null);
    const form = reactive({ minutes: null, km: null, rpe: null, note: "" });
    const openLog = () => {
      form.minutes = props.s.minutes;
      form.km = props.s.distance_m ? +(props.s.distance_m / (props.s.sport === "swim" ? 1 : 1000)).toFixed(props.s.sport === "swim" ? 0 : 1) : null;
      form.rpe = null; logging.value = true;
    };
    const save = async () => {
      busy.value = true;
      try {
        const body = { actual_minutes: form.minutes, rpe: form.rpe, note: form.note || null };
        if (form.km && props.s.sport !== "gym") body.actual_distance_m = props.s.sport === "swim" ? form.km : form.km * 1000;
        await api(`/api/sessions/${props.s.id}/complete`, { method: "POST", body });
        logging.value = false; toast(`Logged: ${props.s.title}`); emit("changed");
      } catch (e) { toast(e.message); } finally { busy.value = false; }
    };
    const quickDone = async () => {
      busy.value = true;
      try { await api(`/api/sessions/${props.s.id}/complete`, { method: "POST", body: {} }); toast(`Logged: ${props.s.title}`); emit("changed"); }
      catch (e) { toast(e.message); } finally { busy.value = false; }
    };
    const miss = async () => {
      busy.value = true;
      try {
        const r = await api(`/api/sessions/${props.s.id}/miss`, { method: "POST" });
        changes.value = r.changes; emit("changed", r.changes);
      } catch (e) { toast(e.message); } finally { busy.value = false; }
    };
    const reset = async () => { await api(`/api/sessions/${props.s.id}/reset`, { method: "POST" }); emit("changed"); };
    // life happens: no equipment today, or only a few minutes
    const adapting = ref(false);
    const adapt = async (body) => {
      busy.value = true;
      try { await api(`/api/sessions/${props.s.id}/adapt`, { method: "POST", body }); adapting.value = false; emit("changed"); }
      catch (e) { toast(e.message); }
      busy.value = false;
    };
    const restore = async () => { await api(`/api/sessions/${props.s.id}/restore`, { method: "POST" }); emit("changed"); };
    const checking = ref(false);
    return { logging, busy, form, openLog, save, quickDone, miss, reset, changes, checking, adapting, adapt, restore, CHECK_HELP, fmtMin, fmtDist, fmtDay, SPORTS };
  },
  template: `
  <article class="workout" :class="'s-' + s.sport">
    <header>
      <div>
        <p v-if="showDay" class="muted" style="margin:0 0 2px">{{ fmtDay(s.day) }}</p>
        <h2>{{ s.title }}</h2>
        <p class="meta">
          <span v-if="s.start_time"><b>{{ s.start_time }}</b></span>
          <span><b>{{ fmtMin(s.status === 'done' ? s.actual_minutes : s.minutes) }}</b></span>
          <span v-if="s.distance_m && s.sport !== 'gym'"><b>{{ fmtDist(s.status === 'done' && s.actual_distance_m != null ? s.actual_distance_m : s.distance_m, s.sport) }}</b></span>
          <span><b>{{ Math.round(s.status === 'done' ? s.actual_tss : s.tss) }}</b> TSS</span>
          <span>{{ SPORTS[s.sport] }}</span>
        </p>
      </div>
      <span v-if="s.status === 'done'" class="done-tag">Done{{ s.rpe ? ' · RPE ' + s.rpe : '' }}</span>
      <span v-else-if="s.status === 'missed'" class="missed-tag">Missed</span>
    </header>
    <p v-if="s.note" class="muted" style="margin:8px 0 0;font-size:.9rem">{{ s.note }}</p>
    <Profile v-if="s.profile && s.profile.length > 1" :profile="s.profile" />
    <WorkoutSteps :steps="s.steps" />
    <details v-if="fuel && fuel.length" class="fuel-details" :open="s.minutes > 60"><summary>Fuel for this session</summary><FuelPlan :fuel="fuel" /></details>

    <div v-if="s.kind === 'test' && s.status === 'planned'" class="levelcheck-in-card">
      <p class="muted">{{ CHECK_HELP[s.sport] }}</p>
      <LevelCheckForm v-if="checking" :sport="s.sport" :session-id="s.id" @placed="$emit('changed')" />
      <div v-else class="actions"><button class="btn" @click="checking = true">Log my result</button><button class="btn miss" :disabled="busy" @click="miss">Can't do it this week</button></div>
    </div>
    <div v-else-if="s.status === 'planned' && !logging" class="actions">
      <button class="btn" :disabled="busy" @click="quickDone">Done as planned</button>
      <button class="btn ghost" :disabled="busy" @click="openLog">Log details</button>
      <button class="btn miss" :disabled="busy" @click="miss">I missed this</button>
      <button v-if="!adapting" class="btn ghost small" @click="adapting = true">Change for today</button>
    </div>
    <div v-if="adapting && s.status === 'planned'" class="adapt-row">
      <span class="muted">Only have</span>
      <button v-for="m in [15, 20, 30]" :key="m" v-show="m < s.minutes" class="chip" :disabled="busy" @click="adapt({ minutes: m })">{{ m }} min</button>
      <template v-if="s.sport === 'gym'"><span class="muted">or</span>
        <button class="chip" :disabled="busy" @click="adapt({ equipment: 'none' })">No equipment</button>
        <button class="chip" :disabled="busy" @click="adapt({ equipment: 'home' })">Dumbbells only</button></template>
      <button class="link" @click="adapting = false">Cancel</button>
    </div>
    <p v-if="s.adapted && s.status === 'planned'" class="adapt-row"><span class="muted">Changed for today.</span><button class="link" @click="restore">Undo</button></p>
    <div v-else-if="s.status !== 'planned' && !logging" class="actions">
      <button class="btn ghost small" @click="reset">Mark as not done</button>
    </div>

    <form v-if="logging" class="log-form" @submit.prevent="save">
      <label class="field">Time (min)<input type="number" min="1" v-model.number="form.minutes"></label>
      <label v-if="s.sport !== 'gym'" class="field">Distance ({{ s.sport === 'swim' ? 'm' : 'km' }})<input type="number" step="any" min="0" v-model.number="form.km"></label>
      <div class="field full">How hard did it feel? (RPE)
        <div class="rpe-row"><button type="button" v-for="n in 10" :key="n" :class="{ on: form.rpe === n }" @click="form.rpe = n">{{ n }}</button></div>
      </div>
      <label class="field full">Note<input type="text" v-model="form.note" placeholder="Legs heavy, windy…"></label>
      <div class="full actions" style="margin:0"><button class="btn" :disabled="busy">Save workout</button>
        <button type="button" class="btn ghost" @click="logging = false">Cancel</button></div>
    </form>

    <TransitionGroup v-if="changes" name="cascade" tag="ul" class="changes" aria-live="polite" appear>
      <li v-for="(c, i) in changes" :key="i" :style="{ transitionDelay: i * 60 + 'ms' }"><span class="a" :class="'a-' + c.action">{{ c.action }}</span><span>{{ c.detail }}</span></li>
    </TransitionGroup>
  </article>`,
};

/* ================================================================ load chart */
const LoadChart = {
  props: { points: Array, height: { type: Number, default: 280 } },
  setup(props) {
    const W = 900, H = props.height, padL = 34, padR = 10, top = 10, mainH = H - 100, tsbH = 56, tsbTop = H - tsbH - 22;
    const hover = ref(null);
    const wrap = ref(null);
    const geo = computed(() => {
      const pts = props.points || [];
      if (!pts.length) return null;
      const n = pts.length;
      const maxY = Math.max(20, ...pts.map((p) => Math.max(p.ctl, p.atl))) * 1.08;
      const maxT = Math.max(15, ...pts.map((p) => Math.abs(p.tsb)));
      const x = (i) => padL + (i / Math.max(1, n - 1)) * (W - padL - padR);
      const y = (v) => top + mainH - (v / maxY) * mainH;
      const yt = (v) => tsbTop + tsbH / 2 - (v / maxT) * (tsbH / 2);
      const split = pts.findIndex((p) => p.projected);
      const line = (key, from, to) => pts.slice(from, to).map((p, i) => `${i ? "L" : "M"}${x(i + from).toFixed(1)},${y(p[key]).toFixed(1)}`).join("");
      const cut = split === -1 ? n : split;
      const ticks = [];
      for (let v = 0; v <= maxY; v += maxY > 90 ? 25 : 10) ticks.push({ v, y: y(v) });
      const months = [];
      pts.forEach((p, i) => { if (p.day.endsWith("-01")) months.push({ x: x(i), label: parseISO(p.day).toLocaleDateString(undefined, { month: "short" }) }); });
      const bw = Math.max(1, (W - padL - padR) / n - 0.6);
      const bars = pts.map((p, i) => ({ x: x(i) - bw / 2, y: Math.min(yt(0), yt(p.tsb)), h: Math.abs(yt(p.tsb) - yt(0)), pos: p.tsb >= 0, proj: p.projected }));
      return { x, y, yt, n, ctlPast: line("ctl", 0, cut), ctlProj: cut < n ? line("ctl", Math.max(0, cut - 1), n) : "",
        atlPast: line("atl", 0, cut), atlProj: cut < n ? line("atl", Math.max(0, cut - 1), n) : "",
        todayX: split > 0 ? x(split) : null, ticks, months, bars, bw };
    });
    const move = (ev) => {
      const g = geo.value; if (!g) return;
      const r = ev.currentTarget.getBoundingClientRect();
      const px = ((ev.clientX - r.left) / r.width) * W;
      const i = Math.round(((px - padL) / (W - padL - padR)) * (g.n - 1));
      if (i < 0 || i >= g.n) { hover.value = null; return; }
      hover.value = { i, p: props.points[i], left: (g.x(i) / W) * r.width, top: (g.y(props.points[i].ctl) / H) * r.height };
    };
    return { W, H, geo, hover, move, padL, tsbTop, tsbH, fmtDay, wrap };
  },
  template: `
  <div class="chart-wrap" ref="wrap">
    <div class="chart-legend">
      <span><i style="background:var(--ink)"></i>Fitness (CTL, 42-day)</span>
      <span><i style="background:var(--run)"></i>Fatigue (ATL, 7-day)</span>
      <span><i style="background:var(--good)"></i>Form (TSB)</span>
      <span><i style="background:repeating-linear-gradient(90deg,var(--muted) 0 4px,transparent 4px 7px)"></i>Planned</span>
    </div>
    <svg v-if="geo" class="load" :viewBox="'0 0 ' + W + ' ' + H" @mousemove="move" @mouseleave="hover = null" role="img" aria-label="Fitness, fatigue and form over the season">
      <g v-for="t in geo.ticks" :key="t.v"><line :x1="padL" :x2="W" :y1="t.y" :y2="t.y" stroke="var(--line-soft)"/><text :x="padL - 6" :y="t.y + 4" text-anchor="end">{{ t.v }}</text></g>
      <g v-for="m in geo.months" :key="m.x"><line :x1="m.x" :x2="m.x" :y1="10" :y2="H - 18" stroke="var(--line-soft)" opacity=".6"/><text :x="m.x + 3" :y="H - 4">{{ m.label }}</text></g>
      <path :d="geo.atlPast" fill="none" stroke="var(--run)" stroke-width="1.6" opacity=".85"/>
      <path :d="geo.atlProj" fill="none" stroke="var(--run)" stroke-width="1.4" stroke-dasharray="4 4" opacity=".55"/>
      <path :d="geo.ctlPast" fill="none" stroke="var(--ink)" stroke-width="2.6"/>
      <path :d="geo.ctlProj" fill="none" stroke="var(--ink)" stroke-width="2" stroke-dasharray="5 4" opacity=".6"/>
      <line :x1="padL" :x2="W" :y1="geo.yt(0)" :y2="geo.yt(0)" stroke="var(--line)"/>
      <text :x="padL - 6" :y="geo.yt(0) + 4" text-anchor="end">form</text>
      <rect v-for="(b, i) in geo.bars" :key="i" :x="b.x" :y="b.y" :width="geo.bw" :height="Math.max(.5, b.h)"
        :fill="b.pos ? 'var(--good)' : 'var(--run)'" :opacity="b.proj ? .35 : .8"/>
      <g v-if="geo.todayX"><line :x1="geo.todayX" :x2="geo.todayX" y1="4" :y2="H - 18" stroke="var(--ink)" stroke-dasharray="2 3"/>
        <text :x="geo.todayX + 4" y="14" style="fill:var(--ink);font-weight:600">Today</text></g>
      <line v-if="hover" :x1="geo.x(hover.i)" :x2="geo.x(hover.i)" y1="4" :y2="H - 18" stroke="var(--muted)"/>
    </svg>
    <div v-if="hover" class="tip" :style="{ left: hover.left + 'px', top: hover.top + 30 + 'px' }">
      <b>{{ fmtDay(hover.p.day, { weekday: 'short', month: 'short', day: 'numeric' }) }}</b>{{ hover.p.projected ? ' (planned)' : '' }}<br>
      Fitness {{ hover.p.ctl }} · Fatigue {{ hover.p.atl }} · Form {{ hover.p.tsb > 0 ? '+' : '' }}{{ hover.p.tsb }}<br>Load {{ hover.p.tss }} TSS
    </div>
  </div>`,
};

/* ================================================================ season strip */
const SeasonStrip = {
  props: { weeks: Array, compact: Boolean, current: String },
  emits: ["pick"],
  setup(props) {
    const max = computed(() => Math.max(1, ...props.weeks.map((w) => Math.max(w.planned_tss, w.target_tss))));
    const blocks = computed(() => {
      const out = [];
      props.weeks.forEach((w) => { const last = out.at(-1); if (last && last.phase === w.phase) last.n++; else out.push({ phase: w.phase, n: 1 }); });
      return out;
    });
    return { max, blocks };
  },
  template: `
  <div>
    <div class="strip">
      <button v-for="w in weeks" :key="w.index" class="wk" :class="['ph-' + w.phase, { rec: w.recovery, now: w.start === current }]"
        :title="'Week ' + (w.index + 1) + ' · ' + w.phase + (w.recovery ? ' (deload)' : '') + ' · ' + w.planned_tss + ' TSS · ' + Math.round(w.minutes / 60 * 10) / 10 + ' h'"
        @click="$emit('pick', w)">
        <span class="bar" :style="{ height: (w.planned_tss / max * 100) + '%' }"></span>
      </button>
    </div>
    <div v-if="!compact" class="phase-axis">
      <span v-for="(b, i) in blocks" :key="i" :class="'ph-' + b.phase" :style="{ gridColumn: 'span ' + b.n }">{{ b.n > 1 ? b.phase : '' }}</span>
    </div>
  </div>`,
};

/* ================================================================ views */
const LanesArt = {
  template: `<svg class="lanes-art" viewBox="0 0 800 300" preserveAspectRatio="none" aria-hidden="true">
    <g fill="none" stroke-width="2">
      <path v-for="i in 7" :key="i" :d="'M -20 ' + (300 - i * 6) + ' C 260 ' + (40 + i * 26) + ', 560 ' + (40 + i * 26) + ', 820 ' + (300 - i * 6)"
        :stroke="i === 4 ? '#e2531b' : 'rgba(255,255,255,.18)'" :stroke-width="i === 4 ? 3 : 1.5"/>
    </g></svg>`,
};

const Welcome = {
  components: { LanesArt },
  setup() {
    const demos = ref([]);
    const mode = ref("signup");
    const form = reactive({ name: "", email: "", password: "" });
    const err = ref("");
    onMounted(async () => { demos.value = await api("/api/demo/profiles"); if (demos.value.length) mode.value = "demo"; });
    const demo = async (k) => { err.value = ""; try { setAuth(await api(`/api/auth/demo/${k}`, { method: "POST" })); go("/today"); } catch (e) { err.value = e.message; } };
    const submit = async () => {
      err.value = "";
      try {
        const r = mode.value === "signup"
          ? await api("/api/auth/register", { method: "POST", body: form })
          : await api("/api/auth/login", { method: "POST", body: { email: form.email, password: form.password } });
        setAuth(r); go(mode.value === "signup" ? "/setup" : "/today");
      } catch (e) { err.value = e.message; }
    };
    return { demos, mode, form, err, demo, submit };
  },
  template: `
  <div class="welcome">
    <section class="hero">
      <div style="display:flex;align-items:center;gap:10px;font-family:var(--cond);font-weight:700;font-size:1.6rem">Taper</div>
      <div>
        <h1>Train for the race. Eat for the training.</h1>
        <p>One planner for hybrid athletes: running, cycling, swimming and lifting, fitted around your classes and shifts. Every day you get the session, the calories and macros it needs, and when to eat them.</p>
      </div>
      <LanesArt />
    </section>
    <section class="side">
      <div class="tabs" role="tablist">
        <button v-if="demos.length" :class="{ on: mode === 'demo' }" @click="mode = 'demo'">Try a demo athlete</button>
        <button :class="{ on: mode === 'login' }" @click="mode = 'login'">Sign in</button>
        <button :class="{ on: mode === 'signup' }" @click="mode = 'signup'">Create account</button>
      </div>
      <div v-if="mode === 'demo'" class="demo-list">
        <button v-for="d in demos" :key="d.key" class="demo" @click="demo(d.key)"><b>{{ d.name }}</b><span>{{ d.blurb }}</span></button>
      </div>
      <form v-else class="auth-form" @submit.prevent="submit">
        <label v-if="mode === 'signup'" class="field">Name<input type="text" v-model="form.name" required autocomplete="name"></label>
        <label class="field">Email<input type="email" v-model="form.email" required autocomplete="email"></label>
        <label class="field">Password<input type="password" v-model="form.password" required minlength="8" :autocomplete="mode === 'signup' ? 'new-password' : 'current-password'"></label>
        <button class="btn">{{ mode === 'signup' ? 'Create account' : 'Sign in' }}</button>
      </form>
      <p v-if="err" class="err">{{ err }}</p>
    </section>
  </div>`,
};

/* ================================================================ nutrition pieces */
const MEAL_LABEL = { breakfast: "Breakfast", lunch: "Lunch", dinner: "Dinner", snack: "Snacks", training: "Training fuel" };
const SOURCE_LABEL = { mine: "My foods & recipes", cnf: "Canadian Nutrient File", off: "Open Food Facts", usda: "USDA FoodData Central", custom: "My foods", recipe: "My recipe" };
const LOAD_LABEL = { rest: "Rest day", light: "Light day", moderate: "Moderate day", high: "Big day", very_high: "Very big day" };
const r0 = (x) => Math.round(x || 0);
const fmtK = (x) => r0(x).toLocaleString();

const FuelPlan = {
  props: { fuel: Array, compact: Boolean },
  setup(props) {
    const rows = computed(() => {
      const f = props.fuel || [];
      const doses = f.filter((s) => s.dose);
      const out = f.filter((s) => !s.dose);
      if (doses.length) {
        const i = out.findIndex((s) => s.phase === "during");
        const every = doses.length > 1 ? doses[1].offset_min - doses[0].offset_min : 30;
        out.splice(i + 1, 0, { phase: "during", at: doses[0].at, offset_min: doses[0].offset_min,
          text: `${doses[0].carbs_g} g carbs every ${every} min, from minute ${doses[0].offset_min} (${doses.length} doses${doses[0].at ? ", first at " + doses[0].at : ""})` });
      }
      return out;
    });
    const label = { before: "Before", during: "During", after: "After" };
    return { rows, label };
  },
  template: `
  <ol class="fuel" :class="{ compact }">
    <li v-for="(s, i) in rows" :key="i" :class="'ph-' + s.phase">
      <span class="when">{{ s.at || label[s.phase] }}</span>
      <span class="what"><b v-if="!compact">{{ label[s.phase] }}.</b> {{ s.text }}</span>
    </li>
  </ol>`,
};

const MacroBars = {
  props: { target: Object, totals: Object },
  setup(props) {
    const rows = computed(() => [
      ["Protein", "protein", props.target.protein_g, "p"],
      ["Carbs", "carbs", props.target.carbs_g, "c"],
      ["Fat", "fat", props.target.fat_g, "f"],
    ].map(([label, k, t, cls]) => {
      const v = props.totals?.[k] || 0;
      return { label, v: r0(v), t, cls, pct: Math.min(100, (v / (t || 1)) * 100), over: v > t * 1.1 };
    }));
    const kcalPct = computed(() => Math.min(1, (props.totals?.kcal || 0) / (props.target.kcal || 1)));
    return { rows, kcalPct, fmtK };
  },
  template: `
  <div class="macros">
    <div class="kcal-ring" :style="{ '--p': kcalPct }" role="img" :aria-label="fmtK(totals.kcal) + ' of ' + fmtK(target.kcal) + ' kcal'">
      <svg viewBox="0 0 120 120"><circle cx="60" cy="60" r="52" class="bg"/><circle cx="60" cy="60" r="52" class="fg" :style="{ strokeDashoffset: 327 * (1 - kcalPct) }"/></svg>
      <div><span class="num">{{ fmtK(totals.kcal) }}</span><small>of {{ fmtK(target.kcal) }} kcal</small></div>
    </div>
    <div class="mbars">
      <div v-for="r in rows" :key="r.label" class="mbar" :class="[r.cls, { over: r.over }]">
        <span>{{ r.label }}</span><span class="num">{{ r.v }} / {{ r.t }} g</span>
        <span class="track"><i :style="{ width: r.pct + '%' }"></i></span>
      </div>
    </div>
  </div>`,
};

const SetupNeeded = {
  props: { missing: Array },
  setup: () => ({ go }),
  template: `
  <div class="setup-card">
    <h3>Calorie targets need a few facts about you</h3>
    <p class="muted">Missing: {{ missing.map(m => ({ sex: 'sex', birth_year: 'birth year', height_cm: 'height', weight: "today's weight" }[m])).join(', ') }}. Nothing is estimated until you add them.</p>
    <button class="btn small" @click="go('/setup')">Add them</button>
  </div>`,
};

const FuelCard = {
  components: { MacroBars, SetupNeeded, FuelPlan },
  props: { n: Object },
  setup: () => ({ fmtK, fmtDay, LOAD_LABEL, go }),
  template: `
  <section class="fuel-card">
    <div class="fc-head"><h3>Fuel today</h3><a href="#/food" class="muted">Log food</a></div>
    <SetupNeeded v-if="n.setup_needed" :missing="n.setup_needed" />
    <template v-else>
      <p class="fc-why">{{ LOAD_LABEL[n.target.load] }}: base {{ fmtK(n.target.base_kcal) }} + training {{ fmtK(n.target.training_kcal) }} {{ n.target.goal_adjust_kcal < 0 ? '−' : '+' }} {{ fmtK(Math.abs(n.target.goal_adjust_kcal)) }} for your goal</p>
      <MacroBars :target="n.target" :totals="n.eaten.totals" />
      <p v-for="(t, i) in n.target.notes" :key="i" class="fc-note">{{ t }}</p>
      <div class="tomorrow">
        <h4>Tomorrow · {{ fmtK(n.tomorrow.target.kcal) }} kcal</h4>
        <p v-if="n.tomorrow.sessions.length">
          <template v-for="(s, i) in n.tomorrow.sessions" :key="i">{{ i ? ' + ' : '' }}{{ s.session.title }}{{ s.session.start_time ? ' at ' + s.session.start_time : '' }}</template>
          → {{ n.tomorrow.target.training_kcal }} kcal of training, {{ n.tomorrow.target.carbs_g }} g carbs.
        </p>
        <p v-else>Rest day → {{ n.tomorrow.target.carbs_g }} g carbs, {{ n.tomorrow.target.protein_g }} g protein.</p>
      </div>
    </template>
  </section>`,
};

const Checklist = {
  props: { day: String },
  emits: ["changed"],
  setup(props, { emit }) {
    const c = ref(null), note = ref("");
    const load = async () => { c.value = await api(`/api/checklist?d=${props.day}`); note.value = c.value.reflection || ""; };
    onMounted(load);
    const just = ref(null);
    const toggle = async (it) => {
      just.value = it.done ? null : it.key;      // the check pops only when you tick it, not on page load
      try {
        if (it.session_id) {
          await api(`/api/sessions/${it.session_id}/${it.done ? "reset" : "complete"}`, { method: "POST", body: it.done ? undefined : {} });
          await load(); emit("changed"); return;
        }
        if (it.auto) return toast("This one ticks itself from your logs.");
        const r = await api(`/api/checklist/${props.day}`, { method: "POST", body: { key: it.key, done: !it.done } });
        c.value = { ...r, history: c.value.history };
      } catch (e) { toast(e.message); }
    };
    const saveNote = () => api(`/api/day/${props.day}/note`, { method: "PUT", body: { reflection: note.value || null } });
    return { c, toggle, note, saveNote, just };
  },
  template: `
  <section v-if="c" class="checklist">
    <div class="fc-head"><h3>Today's goals</h3><span class="score num">{{ c.done }}/{{ c.total }}</span></div>
    <ul>
      <li v-for="it in c.items" :key="it.key" :class="['k-' + it.kind, { done: it.done, just: just === it.key }]">
        <button class="tick" :aria-pressed="it.done" @click="toggle(it)" :title="it.auto && !it.session_id ? 'Ticks itself from your logs' : ''">
          <span class="box" :class="it.sport ? 's-' + it.sport : ''"></span><span>{{ it.label }}</span>
          <small v-if="it.auto && !it.session_id">auto</small>
        </button>
      </li>
    </ul>
    <div class="hist" aria-label="Last 14 days">
      <span v-for="h in c.history" :key="h.date" :style="{ height: Math.max(6, h.score) + '%' }" :title="h.date + ': ' + h.score + '%'"></span>
    </div>
    <label class="field reflect">Evening note<textarea rows="2" v-model="note" @blur="saveNote" placeholder="What went well, what to change tomorrow"></textarea></label>
  </section>`,
};

/* ================================================================ food log */
const FoodSearch = {
  props: { meal: String, day: String, pickOnly: Boolean },
  emits: ["added", "close", "picked"],
  setup(props, { emit }) {
    const q = ref(""), res = ref(null), recent = ref([]), busy = ref(false), picked = ref(null), grams = ref(100);
    const tab = ref("search"), quick = reactive({ name: "", kcal: null, protein: null, carbs: null, fat: null });
    const custom = reactive({ name: "", brand: "", basis_g: 100, kcal: null, protein: null, carbs: null, fat: null, serving_g: null, serving_label: "" });
    const scan = reactive({ supported: "BarcodeDetector" in window, on: false, code: "" });
    let stream = null;
    onMounted(async () => { recent.value = await api("/api/foods/recent"); });
    onUnmounted(() => stopScan());
    const search = async () => {
      if (q.value.trim().length < 2) return;
      busy.value = true; res.value = null;
      try { res.value = await api(`/api/foods/search?q=${encodeURIComponent(q.value)}`); } catch (e) { toast(e.message); }
      busy.value = false;
    };
    const pick = async (f) => {
      if (f.source === "cnf" && !f.per100) {
        busy.value = true;
        try { f = await api(`/api/foods/cnf/${f.source_id}`); } catch (e) { busy.value = false; return toast(e.message); }
        busy.value = false;
      }
      if (!f.complete) toast("Some nutrients are missing in the source database — only what's there is counted.");
      picked.value = f;
      grams.value = f.usual_grams || f.servings?.[0]?.grams || 100;
    };
    const per = (k) => picked.value?.per100?.[k] == null ? null : (picked.value.per100[k] * grams.value) / 100;
    const add = async () => {
      if (props.pickOnly) { emit("picked", { food: picked.value, grams: grams.value }); picked.value = null; return; }
      try {
        await api("/api/log", { method: "POST", body: { day: props.day, meal: props.meal, food_id: picked.value.id, grams: grams.value } });
        toast(`Added ${picked.value.name}`); emit("added"); picked.value = null;
      } catch (e) { toast(e.message); }
    };
    const addQuick = async () => {
      try { await api("/api/log", { method: "POST", body: { day: props.day, meal: props.meal, ...quick } }); toast(`Added ${quick.name}`); emit("added"); emit("close"); }
      catch (e) { toast(e.message); }
    };
    const addCustom = async () => {
      try { const f = await api("/api/foods", { method: "POST", body: { ...custom, serving_g: custom.serving_g || null } }); tab.value = "search"; pick(f); }
      catch (e) { toast(e.message); }
    };
    const lookup = async (code) => {
      try { pick(await api(`/api/foods/barcode/${code}`)); stopScan(); } catch (e) { toast(e.message); }
    };
    const startScan = async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
        scan.on = true;
        await new Promise((r) => setTimeout(r, 50));
        const v = document.getElementById("scanvid"); v.srcObject = stream; await v.play();
        const det = new window.BarcodeDetector({ formats: ["ean_13", "ean_8", "upc_a", "upc_e"] });
        const loop = async () => {
          if (!scan.on) return;
          const codes = await det.detect(v).catch(() => []);
          if (codes.length) return lookup(codes[0].rawValue);
          requestAnimationFrame(loop);
        };
        loop();
      } catch { toast("Camera unavailable. Type the barcode instead."); stopScan(); }
    };
    function stopScan() { scan.on = false; stream?.getTracks().forEach((t) => t.stop()); stream = null; }
    const groups = computed(() => !res.value ? [] : ["mine", "cnf", "off", "usda"].map((k) => ({ k, items: res.value.results[k] || [], status: k === "mine" ? "ok" : res.value.sources[k] })));
    return { q, res, recent, busy, picked, grams, search, pick, per, add, tab, quick, addQuick, custom, addCustom, scan, startScan, stopScan, lookup, groups, MEAL_LABEL, SOURCE_LABEL, r0 };
  },
  template: `
  <div class="scrim" @click.self="$emit('close')">
    <div class="drawer food-drawer" role="dialog" aria-modal="true">
      <button class="btn ghost small close" @click="$emit('close')">Close</button>
      <h2>{{ pickOnly ? 'Add an ingredient' : 'Add to ' + MEAL_LABEL[meal].toLowerCase() }}</h2>
      <div class="tabs" style="margin-top:14px">
        <button :class="{ on: tab === 'search' }" @click="tab = 'search'">Search</button>
        <button :class="{ on: tab === 'scan' }" @click="tab = 'scan'">Barcode</button>
        <button v-if="!pickOnly" :class="{ on: tab === 'quick' }" @click="tab = 'quick'">Quick add</button>
        <button :class="{ on: tab === 'custom' }" @click="tab = 'custom'">New food</button>
      </div>

      <div v-if="picked" class="portion">
        <h3>{{ picked.name }}</h3>
        <p class="muted">{{ picked.brand ? picked.brand + ' · ' : '' }}{{ SOURCE_LABEL[picked.source] }}{{ picked.per100.kcal != null ? ' · ' + r0(picked.per100.kcal) + ' kcal / 100 g' : '' }}</p>
        <div class="chips">
          <button v-for="s in picked.servings" :key="s.label" class="chip" @click="grams = s.grams">{{ s.label }} ({{ r0(s.grams) }} g)</button>
          <button class="chip" @click="grams = 100">100 g</button>
        </div>
        <label class="field">Amount (g)<input type="number" min="1" step="any" v-model.number="grams"></label>
        <p class="portion-sum num">{{ per('kcal') == null ? '—' : r0(per('kcal')) }} kcal · P {{ per('protein') == null ? '—' : r0(per('protein')) }} · C {{ per('carbs') == null ? '—' : r0(per('carbs')) }} · F {{ per('fat') == null ? '—' : r0(per('fat')) }}</p>
        <div class="actions"><button class="btn" @click="add">{{ pickOnly ? 'Use' : 'Add' }} {{ r0(grams) }} g</button><button class="btn ghost" @click="picked = null">Back</button></div>
      </div>

      <template v-else-if="tab === 'search'">
        <form class="search-row" @submit.prevent="search">
          <input type="text" v-model="q" placeholder="poulet rôti, riz, Greek yogurt…" aria-label="Search foods" autofocus>
          <button class="btn" :disabled="busy">{{ busy ? 'Searching…' : 'Search' }}</button>
        </form>
        <div v-if="!res && recent.length" class="results">
          <h4>Recent</h4>
          <button v-for="f in recent" :key="f.id" class="food-row" @click="pick(f)">
            <span><b>{{ f.name }}</b><small>{{ f.brand || SOURCE_LABEL[f.source] }} · usually {{ f.usual_grams }} g</small></span>
            <span class="num">{{ r0(f.per100.kcal) }}<small>kcal/100 g</small></span>
          </button>
        </div>
        <div v-for="g in groups" :key="g.k" class="results">
          <h4>{{ SOURCE_LABEL[g.k] }} <small v-if="g.status !== 'ok'" class="err">{{ g.status }}</small></h4>
          <p v-if="g.status === 'ok' && !g.items.length" class="muted small">No matches.</p>
          <button v-for="f in g.items" :key="g.k + f.source_id" class="food-row" @click="pick(f)">
            <span><b>{{ f.name }}</b><small>{{ f.brand || '' }}{{ f.complete === false ? ' · incomplete data' : '' }}</small></span>
            <span v-if="f.per100" class="num">{{ f.per100.kcal == null ? '—' : r0(f.per100.kcal) }}<small>kcal/100 g</small></span>
            <span v-else class="muted small">tap for nutrients</span>
          </button>
        </div>
        <p class="attrib">Food data: Canadian Nutrient File (Health Canada, Open Government Licence – Canada), Open Food Facts (ODbL), USDA FoodData Central (public domain).</p>
      </template>

      <div v-else-if="tab === 'scan'" class="scan">
        <template v-if="scan.supported">
          <video v-show="scan.on" id="scanvid" playsinline muted></video>
          <button v-if="!scan.on" class="btn" @click="startScan">Open camera</button>
          <button v-else class="btn ghost" @click="stopScan">Stop</button>
        </template>
        <p v-else class="muted">This browser can't scan barcodes with the camera (Safari on iPhone doesn't support it yet). Type the number under the barcode instead.</p>
        <form class="search-row" @submit.prevent="lookup(scan.code)">
          <input type="text" inputmode="numeric" v-model="scan.code" placeholder="Barcode number" aria-label="Barcode number">
          <button class="btn">Look up</button>
        </form>
      </div>

      <form v-else-if="tab === 'quick'" class="form-grid" @submit.prevent="addQuick">
        <p class="muted full">For a restaurant meal or anything you only know the totals of.</p>
        <label class="field full">What<input type="text" v-model="quick.name" required placeholder="Poutine, Lanterne Rouge"></label>
        <label class="field">Calories<input type="number" min="0" v-model.number="quick.kcal" required></label>
        <label class="field">Protein (g)<input type="number" min="0" step="any" v-model.number="quick.protein"></label>
        <label class="field">Carbs (g)<input type="number" min="0" step="any" v-model.number="quick.carbs"></label>
        <label class="field">Fat (g)<input type="number" min="0" step="any" v-model.number="quick.fat"></label>
        <div class="full"><button class="btn">Add</button></div>
      </form>

      <form v-else class="form-grid" @submit.prevent="addCustom">
        <p class="muted full">Not in any database? Copy the nutrition label. It's saved to your foods permanently. Values can be per serving; tell us how many grams that is. For a dish made of several ingredients, <a href="#/myfoods?new=recipe">build a recipe</a>.</p>
        <label class="field full">Name<input type="text" v-model="custom.name" required></label>
        <label class="field">Brand<input type="text" v-model="custom.brand"></label>
        <label class="field">Values are for (g)<input type="number" min="1" step="any" v-model.number="custom.basis_g" required></label>
        <label class="field">Calories<input type="number" min="0" step="any" v-model.number="custom.kcal" required></label>
        <label class="field">Protein (g)<input type="number" min="0" step="any" v-model.number="custom.protein"></label>
        <label class="field">Carbs (g)<input type="number" min="0" step="any" v-model.number="custom.carbs"></label>
        <label class="field">Fat (g)<input type="number" min="0" step="any" v-model.number="custom.fat"></label>
        <label class="field">Serving name<input type="text" v-model="custom.serving_label" placeholder="1 scoop"></label>
        <label class="field">Serving (g)<input type="number" min="1" step="any" v-model.number="custom.serving_g"></label>
        <div class="full"><button class="btn">Save food</button></div>
      </form>
    </div>
  </div>`,
};

const FoodView = {
  components: { MacroBars, SetupNeeded, FoodSearch, FuelPlan },
  setup() {
    const day = ref(route.query.d || iso(new Date()));
    const n = ref(null), adding = ref(null);
    const load = async () => { n.value = await api(`/api/nutrition/day?d=${day.value}`); };
    watch(day, load); onMounted(load);
    const shift = (k) => { day.value = iso(addDays(parseISO(day.value), k)); };
    const del = async (e) => { await api(`/api/log/${e.id}`, { method: "DELETE" }); load(); };
    const copyYesterday = async (meal) => {
      const r = await api("/api/log/copy", { method: "POST", body: { from_day: iso(addDays(parseISO(day.value), -1)), to_day: day.value, meal } });
      toast(r.copied ? `Copied ${r.copied} item${r.copied > 1 ? "s" : ""} from yesterday` : "Nothing logged for that meal yesterday"); load();
    };
    const toggleComplete = async () => {
      await api(`/api/checklist/${day.value}`, { method: "POST", body: { key: "food_complete", done: !n.value.food_complete } }); load();
    };
    const mealTotal = (items) => items.reduce((a, e) => a + e.kcal, 0);
    return { day, n, adding, shift, del, copyYesterday, toggleComplete, mealTotal, MEAL_LABEL, fmtDay, fmtK, r0, iso };
  },
  template: `
  <div v-if="n">
    <div class="today-head">
      <div><h1>{{ fmtDay(day) }}</h1><p>Food log · <a href="#/myfoods">My foods &amp; recipes</a></p></div>
      <div class="week-nav"><button class="btn ghost small" @click="shift(-1)">Previous day</button>
        <button class="btn ghost small" @click="day = iso(new Date())">Today</button><button class="btn ghost small" @click="shift(1)">Next day</button></div>
    </div>
    <SetupNeeded v-if="n.setup_needed" :missing="n.setup_needed" />
    <div v-else class="food-summary">
      <MacroBars :target="n.target" :totals="n.eaten.totals" />
      <div class="remaining">
        <div><span class="num">{{ fmtK(Math.max(0, n.remaining.kcal)) }}</span><small>kcal left</small></div>
        <div><span class="num">{{ Math.max(0, n.remaining.protein) }}</span><small>g protein left</small></div>
        <div><span class="num">{{ Math.max(0, n.remaining.carbs) }}</span><small>g carbs left</small></div>
      </div>
    </div>
    <div class="meals">
      <section v-for="(items, meal) in n.eaten.meals" :key="meal" class="meal">
        <header><h3>{{ MEAL_LABEL[meal] }}</h3><span class="num muted">{{ fmtK(mealTotal(items)) }} kcal</span></header>
        <ul>
          <li v-for="e in items" :key="e.id">
            <span><b>{{ e.name }}</b><small>{{ e.grams ? r0(e.grams) + ' g · ' : '' }}P {{ r0(e.protein) }} · C {{ r0(e.carbs) }} · F {{ r0(e.fat) }}</small></span>
            <span class="num">{{ e.kcal }}</span>
            <button class="x" @click="del(e)" :aria-label="'Remove ' + e.name">×</button>
          </li>
        </ul>
        <div class="meal-actions"><button class="btn ghost small" @click="adding = meal">Add food</button>
          <button class="btn ghost small" @click="copyYesterday(meal)">Same as yesterday</button></div>
      </section>
    </div>
    <label class="complete-toggle"><input type="checkbox" :checked="n.food_complete" @change="toggleComplete">
      I logged everything I ate today <small class="muted">(only complete days teach Taper your real expenditure)</small></label>
    <template v-if="n.sessions && n.sessions.length">
      <div class="section-h"><h2>Fuel around today's training</h2></div>
      <div v-for="s in n.sessions" :key="s.session.id" class="fuel-block" :class="'s-' + s.session.sport">
        <h3>{{ s.session.title }}{{ s.session.start_time ? ' · ' + s.session.start_time : '' }}</h3>
        <FuelPlan :fuel="s.fuel" />
      </div>
    </template>
    <FoodSearch v-if="adding" :meal="adding" :day="day" @added="load" @close="adding = null" />
  </div>`,
};

/* ================================================================ body */
const WeightChart = {
  props: { entries: Array, trend: Array },
  setup(props) {
    const W = 900, H = 240, pad = 36;
    const g = computed(() => {
      const t = props.trend || [], e = props.entries || [];
      if (t.length < 2) return null;
      const vals = [...t.map((x) => x.kg), ...e.map((x) => x.kg)];
      const lo = Math.floor(Math.min(...vals) - 0.5), hi = Math.ceil(Math.max(...vals) + 0.5);
      const d0 = parseISO(t[0].day).getTime(), d1 = parseISO(t.at(-1).day).getTime();
      const x = (d) => pad + ((parseISO(d).getTime() - d0) / Math.max(1, d1 - d0)) * (W - pad - 10);
      const y = (v) => 10 + (1 - (v - lo) / (hi - lo)) * (H - 40);
      const ticks = []; for (let v = lo; v <= hi; v += Math.max(1, Math.round((hi - lo) / 4))) ticks.push(v);
      return { x, y, ticks, path: t.map((p, i) => `${i ? "L" : "M"}${x(p.day).toFixed(1)},${y(p.kg).toFixed(1)}`).join("") };
    });
    return { W, H, g, pad };
  },
  template: `
  <div class="chart-wrap">
    <div class="chart-legend"><span><i style="background:var(--ink)"></i>Trend (smoothed)</span><span><i style="background:var(--muted);height:8px;width:8px;border-radius:50%"></i>Daily weigh-in</span></div>
    <svg v-if="g" class="load" :viewBox="'0 0 ' + W + ' ' + H" role="img" aria-label="Weight and trend">
      <g v-for="t in g.ticks" :key="t"><line :x1="pad" :x2="W" :y1="g.y(t)" :y2="g.y(t)" stroke="var(--line-soft)"/><text :x="pad - 6" :y="g.y(t) + 4" text-anchor="end">{{ t }}</text></g>
      <circle v-for="e in entries" :key="e.day" :cx="g.x(e.day)" :cy="g.y(e.kg)" r="3" fill="var(--muted)" opacity=".55"/>
      <path :d="g.path" fill="none" stroke="var(--ink)" stroke-width="2.5"/>
    </svg>
    <p v-else class="muted" style="padding:20px">Weigh in for a few days to see your trend.</p>
  </div>`,
};

const BodyForm = {
  emits: ["saved"],
  setup(props, { emit }) {
    const s = ref(null);
    onMounted(async () => { s.value = await api("/api/settings"); });
    const save = async () => {
      try {
        const b = s.value.body, nn = s.value.nutrition;
        s.value = await api("/api/settings", { method: "PUT", body: {
          body: { sex: b.sex, birth_year: b.birth_year ? +b.birth_year : null, height_cm: b.height_cm ? +b.height_cm : null, neat: b.neat },
          nutrition: { mode: nn.mode, rate_pct_per_week: nn.rate_pct_per_week === "" || nn.rate_pct_per_week == null ? null : +nn.rate_pct_per_week,
            protein_g_per_kg: nn.protein_g_per_kg ? +nn.protein_g_per_kg : null } } });
        toast("Saved"); emit("saved");
      } catch (e) { toast(e.message); }
    };
    return { s, save };
  },
  template: `
  <form v-if="s" class="settings-form" @submit.prevent="save">
    <div class="form-row">
      <label class="field">Sex (for the BMR equation)<select v-model="s.body.sex" required><option :value="null" disabled>Choose</option><option value="male">Male</option><option value="female">Female</option></select></label>
      <label class="field">Birth year<input type="number" v-model="s.body.birth_year" min="1920" max="2015" required></label>
      <label class="field">Height (cm)<input type="number" v-model="s.body.height_cm" min="120" max="230" step="0.5" required></label>
      <label class="field">Daily life outside training<select v-model="s.body.neat">
        <option value="low">Mostly sitting</option><option value="moderate">On my feet some (≈ 7–10k steps)</option><option value="high">On my feet a lot (10k+ steps, physical job)</option></select></label>
    </div>
    <div class="form-row">
      <label class="field">Goal<select v-model="s.nutrition.mode"><option value="lose">Lose fat</option><option value="recomp">Recomposition</option><option value="maintain">Maintain</option><option value="gain">Build muscle</option></select></label>
      <label class="field">Rate (% body weight / week)<input type="number" step="0.05" min="-1" max="1" v-model="s.nutrition.rate_pct_per_week" :placeholder="{ lose: '-0.5', recomp: '-0.25', maintain: '0', gain: '0.25' }[s.nutrition.mode] + ' (default)'"></label>
      <label class="field">Protein (g per kg)<input type="number" step="0.05" min="1" max="3.3" v-model="s.nutrition.protein_g_per_kg" :placeholder="{ lose: '2.0', recomp: '2.0', maintain: '1.6', gain: '1.8' }[s.nutrition.mode] + ' (default)'"></label>
    </div>
    <button class="btn">Save</button>
  </form>`,
};

const BodyView = {
  components: { WeightChart, BodyForm },
  setup() {
    const w = ref(null), n = ref(null), kg = ref(null), day = ref(iso(new Date()));
    const load = async () => { [w.value, n.value] = await Promise.all([api("/api/weights"), api("/api/nutrition/day")]); };
    onMounted(load);
    const add = async () => {
      try { await api("/api/weights", { method: "PUT", body: { day: day.value, kg: +kg.value } }); kg.value = null; toast("Weigh-in saved"); load(); }
      catch (e) { toast(e.message); }
    };
    const del = async (d) => { await api(`/api/weights/${d}`, { method: "DELETE" }); load(); };
    return { w, n, kg, day, add, del, load, fmtDay, fmtK };
  },
  template: `
  <div v-if="w">
    <h1 style="margin-bottom:20px">Body</h1>
    <div class="body-grid">
      <form class="weigh" @submit.prevent="add">
        <h3>Weigh in</h3>
        <p class="muted">Same time each morning, after the bathroom, before eating. The trend line filters out water, glycogen and creatine swings.</p>
        <div class="form-row"><label class="field">Date<input type="date" v-model="day" required></label>
          <label class="field">Weight (kg)<input type="number" step="0.1" min="25" max="350" v-model="kg" required></label></div>
        <button class="btn">Save weigh-in</button>
      </form>
      <section v-if="n && !n.setup_needed" class="tdee">
        <h3>Your expenditure</h3>
        <dl>
          <div><dt>Resting (Mifflin-St Jeor)</dt><dd class="num">{{ fmtK(n.expenditure.bmr) }}</dd></div>
          <div><dt>Daily life, no training</dt><dd class="num">{{ fmtK(n.expenditure.base) }}</dd></div>
          <div><dt>Trend weight</dt><dd class="num">{{ n.expenditure.weight_kg }} kg</dd></div>
        </dl>
        <p v-if="n.expenditure.observed" class="muted">Learned from your data: {{ fmtK(n.expenditure.observed) }} kcal from {{ n.expenditure.days_logged }} complete days ({{ Math.round(n.expenditure.confidence * 100) }} % weight vs the formula).</p>
        <p v-else class="muted">Using the formula for now. After 14 days marked "logged everything" plus a few weigh-ins, Taper learns your real expenditure from your intake and weight trend ({{ n.expenditure.days_logged }}/14 days so far).</p>
      </section>
    </div>
    <div class="section-h"><h2>Weight</h2></div>
    <WeightChart :entries="w.entries" :trend="w.trend" />
    <div class="section-h"><h2>About you and your goal</h2></div>
    <BodyForm @saved="load" />
    <details class="howto accuracy"><summary>How accurate are these numbers?</summary>
      <ul>
        <li><b>Resting expenditure</b> (Mifflin-St Jeor) lands within 10 % of a lab measurement for about 4 in 5 non-obese adults.</li>
        <li><b>Running</b> costs about 1 kcal per kg per km above rest, nearly the same at any pace. Individual running economy varies, so your true cost can differ by roughly 5–10 %.</li>
        <li><b>Gym, swimming and cycling without a power meter</b> use average values from the Compendium of Physical Activities. Treat them as ±20–30 %.</li>
        <li><b>Your body adapts.</b> On average only about 72 % of extra exercise energy shows up in total daily expenditure, because the rest of the day quietly gets cheaper.</li>
        <li>That's why the formula is only the starting point. After 14 complete days, Taper corrects your base from your real intake and weight trend, which absorbs all of the above for you.</li>
      </ul></details>
    <div v-if="w.entries.length" class="section-h"><h2>Weigh-ins</h2></div>
    <ul class="wlist"><li v-for="e in [...w.entries].reverse().slice(0, 14)" :key="e.day"><span>{{ fmtDay(e.day, { weekday: 'short', month: 'short', day: 'numeric' }) }}</span><span class="num">{{ e.kg }} kg</span><button class="x" @click="del(e.day)" aria-label="Delete">×</button></li></ul>
  </div>`,
};

const SetupView = {
  components: { BodyForm },
  setup() {
    const kg = ref(null), step = ref(1);
    const saveWeight = async () => {
      try { await api("/api/weights", { method: "PUT", body: { day: iso(new Date()), kg: +kg.value } }); step.value = 3; }
      catch (e) { toast(e.message); }
    };
    return { kg, step, saveWeight, go };
  },
  template: `
  <div class="setup">
    <h1>Set up Taper</h1>
    <p class="muted">Three short steps. Your numbers stay in your account and drive every target.</p>
    <ol class="steps-list">
      <li :class="{ on: step === 1 }"><h3>1 · About you and your goal</h3><BodyForm v-if="step === 1" @saved="step = 2" /></li>
      <li :class="{ on: step === 2 }"><h3>2 · Today's weight</h3>
        <form v-if="step === 2" class="form-row" @submit.prevent="saveWeight"><label class="field">Weight (kg)<input type="number" step="0.1" min="25" max="350" v-model="kg" required></label><div style="align-self:end"><button class="btn">Save</button></div></form></li>
      <li :class="{ on: step === 3 }"><h3>3 · Your calendar and training goal</h3>
        <div v-if="step === 3" class="actions"><button class="btn" @click="go('/settings')">Connect Google Calendar</button><button class="btn ghost" @click="go('/new-plan')">Skip to training goal</button></div></li>
    </ol>
  </div>`,
};

/* ================================================================ settings: calendar, routines, habits */

const MyFoodsView = {
  components: { FoodSearch },
  setup() {
    const foods = ref([]), editing = ref(null), recipe = ref(null), picking = ref(false), preview = ref(null), err = ref("");
    const load = async () => { foods.value = await api("/api/myfoods"); };
    onMounted(async () => { await load(); if (route.query.new === "recipe") newRecipe(); });
    const newRecipe = () => { recipe.value = { id: null, name: "", servings: 1, cooked_weight_g: null, ingredients: [] }; preview.value = null; };
    const editRecipe = (f) => {
      recipe.value = { id: f.id, name: f.name, servings: f.recipe_servings || 1,
        cooked_weight_g: f.recipe_weight_g, ingredients: f.ingredients.map((i) => ({ ...i })) };
      refresh();
    };
    const body = () => ({ name: recipe.value.name || "Recipe", servings: +recipe.value.servings || 1,
      cooked_weight_g: recipe.value.cooked_weight_g ? +recipe.value.cooked_weight_g : null,
      ingredients: recipe.value.ingredients.map((i) => ({ food_id: i.food_id, grams: +i.grams })) });
    let t;
    const refresh = () => {
      clearTimeout(t);
      t = setTimeout(async () => {
        err.value = "";
        if (!recipe.value?.ingredients.length) { preview.value = null; return; }
        try { preview.value = await api("/api/recipes/preview", { method: "POST", body: body() }); } catch (e) { err.value = e.message; preview.value = null; }
      }, 250);
    };
    watch(() => recipe.value && JSON.stringify([recipe.value.servings, recipe.value.cooked_weight_g, recipe.value.ingredients]), refresh);
    const addIngredient = ({ food, grams }) => { recipe.value.ingredients.push({ food_id: food.id, name: food.name, grams }); picking.value = false; };
    const saveRecipe = async () => {
      try {
        const r = recipe.value;
        await api(r.id ? `/api/recipes/${r.id}` : "/api/recipes", { method: r.id ? "PUT" : "POST", body: body() });
        toast(`Saved ${r.name || "recipe"}`); recipe.value = null; load();
      } catch (e) { err.value = e.message; }
    };
    const editFood = (f) => {
      editing.value = { id: f.id, name: f.name, brand: f.brand || "", basis_g: 100, kcal: f.per100.kcal, protein: f.per100.protein,
        carbs: f.per100.carbs, fat: f.per100.fat, serving_label: f.servings[0]?.label || "", serving_g: f.servings[0]?.grams || null };
    };
    const saveFood = async () => {
      try { await api(`/api/foods/${editing.value.id}`, { method: "PUT", body: { ...editing.value, serving_g: editing.value.serving_g || null } }); toast("Saved. Already-logged meals keep their old numbers."); editing.value = null; load(); }
      catch (e) { toast(e.message); }
    };
    const del = async (f) => {
      if (!confirm(`Remove "${f.name}" from your foods? Meals you already logged keep it.`)) return;
      await api(`/api/foods/${f.id}`, { method: "DELETE" }); load();
    };
    const custom = computed(() => foods.value.filter((f) => f.source === "custom"));
    const recipes = computed(() => foods.value.filter((f) => f.source === "recipe"));
    return { foods, custom, recipes, editing, recipe, picking, preview, err, newRecipe, editRecipe, addIngredient, saveRecipe, editFood, saveFood, del, r0, fmtK };
  },
  template: `
  <div>
    <div class="today-head"><div><h1>My foods &amp; recipes</h1><p>Everything here is saved in your account and shows up first when you search.</p></div>
      <div class="week-nav"><a class="btn ghost small" href="#/food">Back to food log</a><button class="btn small" @click="newRecipe">New recipe</button></div></div>

    <section v-if="recipe" class="recipe-editor">
      <h2>{{ recipe.id ? 'Edit recipe' : 'New recipe' }}</h2>
      <div class="form-row">
        <label class="field">Name<input type="text" v-model="recipe.name" placeholder="Bol poulet teriyaki" required></label>
        <label class="field">Makes how many servings<input type="number" min="0.5" step="0.5" v-model="recipe.servings"></label>
        <label class="field">Cooked weight (g, optional)<input type="number" min="1" v-model="recipe.cooked_weight_g" placeholder="weigh the pot"></label>
      </div>
      <p class="muted small">Cooking adds or removes water. If you weigh the finished dish, logging by grams stays accurate; otherwise the raw ingredient total is used.</p>
      <ul class="ing-list">
        <li v-for="(i, k) in recipe.ingredients" :key="k"><span>{{ i.name }}</span>
          <input type="number" min="1" step="any" v-model="i.grams" :aria-label="'Grams of ' + i.name"><span class="muted">g</span>
          <button class="x" @click="recipe.ingredients.splice(k, 1)" :aria-label="'Remove ' + i.name">×</button></li>
      </ul>
      <button class="btn ghost small" @click="picking = true">Add ingredient</button>
      <div v-if="preview" class="recipe-sum">
        <div><span class="num">{{ fmtK(preview.per_serving.kcal) }}</span><small>kcal / serving ({{ preview.servings[0].grams }} g)</small></div>
        <div><span class="num">{{ preview.per_serving.protein ?? '—' }}</span><small>g protein</small></div>
        <div><span class="num">{{ preview.per_serving.carbs ?? '—' }}</span><small>g carbs</small></div>
        <div><span class="num">{{ preview.per_serving.fat ?? '—' }}</span><small>g fat</small></div>
      </div>
      <p v-if="err" class="err">{{ err }}</p>
      <div class="actions"><button class="btn" :disabled="!recipe.ingredients.length" @click="saveRecipe">Save recipe</button><button class="btn ghost" @click="recipe = null">Cancel</button></div>
    </section>

    <div class="section-h"><h2>Recipes</h2></div>
    <p v-if="!recipes.length" class="muted">No recipes yet. Combine foods into a dish you make often, then log it in one tap.</p>
    <ul class="myfood-list">
      <li v-for="f in recipes" :key="f.id"><span><b>{{ f.name }}</b><small>{{ f.ingredients.length }} ingredients · {{ r0(f.servings[0]?.grams) }} g per serving · {{ r0(f.per100.kcal * (f.servings[0]?.grams || 100) / 100) }} kcal</small></span>
        <button class="btn ghost small" @click="editRecipe(f)">Edit</button><button class="btn ghost small" @click="del(f)">Remove</button></li>
    </ul>

    <div class="section-h"><h2>Foods you added</h2></div>
    <p v-if="!custom.length" class="muted">Foods you create from a label (a sauce packet, a local bakery item…) appear here.</p>
    <ul class="myfood-list">
      <li v-for="f in custom" :key="f.id">
        <template v-if="editing && editing.id === f.id">
          <form class="form-grid" style="width:100%" @submit.prevent="saveFood">
            <label class="field full">Name<input type="text" v-model="editing.name" required></label>
            <label class="field">Values are for (g)<input type="number" min="1" step="any" v-model.number="editing.basis_g"></label>
            <label class="field">Calories<input type="number" min="0" step="any" v-model.number="editing.kcal" required></label>
            <label class="field">Protein (g)<input type="number" min="0" step="any" v-model.number="editing.protein"></label>
            <label class="field">Carbs (g)<input type="number" min="0" step="any" v-model.number="editing.carbs"></label>
            <label class="field">Fat (g)<input type="number" min="0" step="any" v-model.number="editing.fat"></label>
            <label class="field">Serving (g)<input type="number" min="1" step="any" v-model.number="editing.serving_g"></label>
            <div class="full actions" style="margin:0"><button class="btn">Save</button><button type="button" class="btn ghost" @click="editing = null">Cancel</button></div>
          </form>
        </template>
        <template v-else>
          <span><b>{{ f.name }}</b><small>{{ f.brand ? f.brand + ' · ' : '' }}{{ r0(f.per100.kcal) }} kcal · P {{ r0(f.per100.protein) }} · C {{ r0(f.per100.carbs) }} · F {{ r0(f.per100.fat) }} per 100 g</small></span>
          <button class="btn ghost small" @click="editFood(f)">Edit</button><button class="btn ghost small" @click="del(f)">Remove</button>
        </template>
      </li>
    </ul>
    <FoodSearch v-if="picking" meal="snack" :day="''" pick-only @picked="addIngredient" @close="picking = false" />
  </div>`,
};

const DataSettings = {
  setup() {
    const info = ref(null), busy = ref(false);
    onMounted(async () => { info.value = await api("/api/storage"); });
    const exportAll = async () => {
      const res = await fetch("/api/export", { headers: { Authorization: `Bearer ${store.token}` } });
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = `taper-backup-${iso(new Date())}.json`; a.click();
      URL.revokeObjectURL(a.href);
    };
    const importFile = async (ev) => {
      const file = ev.target.files[0]; if (!file) return;
      if (!confirm("Importing replaces everything in this account with the backup. Continue?")) { ev.target.value = ""; return; }
      busy.value = true;
      try {
        const data = JSON.parse(await file.text());
        const r = await api("/api/import", { method: "POST", body: { data, confirm_replace: true } });
        toast(`Restored ${r.imported.food_entries} food entries, ${r.imported.sessions} sessions, ${r.imported.weights} weigh-ins`);
      } catch (e) { toast(e.message.includes("JSON") ? "That file isn't a Taper backup" : e.message); }
      busy.value = false; ev.target.value = "";
    };
    return { info, busy, exportAll, importFile };
  },
  template: `
  <section>
    <h2>Your data</h2>
    <p>Everything you log is kept permanently: day 1 is still there on day 800. Foods and recipes you create stay in your account until you remove them.</p>
    <dl v-if="info" class="data-info">
      <div><dt>Saved in</dt><dd><code>{{ info.database }}</code></dd></div>
      <div v-if="info.backups_folder"><dt>Automatic daily backups</dt><dd><code>{{ info.backups_folder }}</code> · {{ info.backup_count }} kept (last 30 days)</dd></div>
    </dl>
    <p v-if="info && info.restore" class="muted small">{{ info.restore }}</p>
    <div class="actions"><button class="btn" @click="exportAll">Download all my data</button>
      <label class="btn ghost" :class="{ disabled: busy }">Restore from a backup file<input type="file" accept="application/json,.json" hidden @change="importFile"></label></div>
  </section>`,
};

const CalendarSettings = {
  setup() {
    const cals = ref([]), url = ref(""), s = ref(null), busy = ref(false), week = ref(null);
    const load = async () => { [cals.value, s.value, week.value] = await Promise.all([api("/api/calendars"), api("/api/settings"), api("/api/calendar/week")]); };
    onMounted(load);
    const add = async () => { busy.value = true; try { await api("/api/calendars", { method: "POST", body: { url: url.value } }); url.value = ""; toast("Calendar connected"); load(); } catch (e) { toast(e.message); } busy.value = false; };
    const sync = async () => { busy.value = true; await api("/api/calendars/sync", { method: "POST" }); await load(); busy.value = false; toast("Calendar refreshed"); };
    const del = async (c) => { await api(`/api/calendars/${c.id}`, { method: "DELETE" }); load(); };
    const saveSchedule = async () => { await api("/api/settings", { method: "PUT", body: { schedule: s.value.schedule } }); toast("Saved"); load(); };
    const replan = async () => { busy.value = true; try { await api("/api/plans/replan", { method: "POST" }); toast("Plan rebuilt around your calendar"); } catch (e) { toast(e.message); } busy.value = false; };
    return { cals, url, s, busy, add, sync, del, saveSchedule, replan, week, fmtMin, DOW };
  },
  template: `
  <section>
    <h2>Calendar</h2>
    <p>Taper reads your Google Calendar to find when you're free. Classes, shifts and study blocks stay protected; exams lighten that week's training automatically.</p>
    <details class="howto"><summary>Where to find your Google Calendar link</summary>
      <ol><li>On a computer, open Google Calendar → gear icon → <b>Settings</b>.</li><li>Under <b>Settings for my calendars</b>, pick your calendar → <b>Integrate calendar</b>.</li>
      <li>Copy <b>Secret address in iCal format</b> and paste it here. Treat it like a password: anyone with it can read your calendar.</li></ol></details>
    <form class="copy-row" style="margin-top:12px" @submit.prevent="add"><input type="text" v-model="url" placeholder="https://calendar.google.com/calendar/ical/…/basic.ics" aria-label="Calendar link"><button class="btn" :disabled="busy || !url">Connect</button></form>
    <ul class="cal-list"><li v-for="c in cals" :key="c.id"><span><b>{{ c.name }}</b> <small class="muted">{{ c.url_hint }}</small></span>
      <span :class="c.ok ? 'ok' : 'err'">{{ c.ok ? 'Synced' : (c.last_error || 'Not synced') }}</span><button class="btn ghost small" @click="del(c)">Remove</button></li></ul>
    <div v-if="cals.length" class="actions"><button class="btn ghost" :disabled="busy" @click="sync">Refresh now</button><button class="btn" :disabled="busy" @click="replan">Rebuild my plan around it</button></div>
    <div v-if="week && cals.length" class="free-week">
      <div v-for="(d, i) in week.days" :key="d.date" :class="{ exam: d.exam }"><b>{{ DOW[i] }}</b><span class="num">{{ fmtMin(d.free_min) }}</span><small>longest {{ fmtMin(d.longest_min) }}{{ d.exam ? ' · exam' : '' }}</small></div>
    </div>
    <form v-if="s" class="form-row" style="margin-top:18px" @submit.prevent="saveSchedule">
      <label class="field">Earliest training<input type="time" v-model="s.schedule.day_start"></label>
      <label class="field">Latest finish<input type="time" v-model="s.schedule.day_end"></label>
      <label class="field">Travel buffer (min)<input type="number" min="0" max="90" v-model.number="s.schedule.buffer_min"></label>
      <label class="field">I prefer training<select v-model="s.schedule.prefer"><option value="morning">Mornings</option><option value="evening">Evenings</option><option value="any">Whenever fits best</option></select></label>
      <div style="align-self:end"><button class="btn ghost">Save</button></div>
    </form>
  </section>`,
};

const RoutineSettings = {
  setup() {
    const routines = ref([]), checks = ref({});
    onMounted(async () => { routines.value = (await api("/api/settings")).gym_routines.map((r) => ({ ...r })); });
    const check = async (r, i) => { const p = await api("/api/dsl/parse", { method: "POST", body: { text: r.dsl } }); checks.value[i] = p.ok ? (p.workout.sport === "gym" ? "ok" : "Start with 'Gym:'") : p.error; };
    const addRow = () => routines.value.push({ name: "", minutes: 90, dsl: "Gym: " });
    const save = async () => {
      try { await api("/api/settings", { method: "PUT", body: { gym_routines: routines.value.filter((r) => r.name).map((r) => ({ name: r.name, minutes: +r.minutes || null, dsl: r.dsl })) } }); toast("Routines saved. Rebuild your plan to use them."); }
      catch (e) { toast(e.message); }
    };
    return { routines, checks, check, addRow, save };
  },
  template: `
  <section>
    <h2>Gym routines</h2>
    <p>Your own split, used in rotation for every strength slot in the plan. Write each one like a workout: <code>Gym: Bench Press 4x8 @ RPE 8 | Incline Chest Press 3x10 @ RPE 8</code>.</p>
    <div v-for="(r, i) in routines" :key="i" class="routine">
      <div class="form-row"><label class="field">Name<input type="text" v-model="r.name" placeholder="Push"></label><label class="field">Real duration (min)<input type="number" min="15" max="240" v-model="r.minutes"></label></div>
      <textarea rows="2" v-model="r.dsl" @blur="check(r, i)" spellcheck="false" aria-label="Exercises"></textarea>
      <small :class="checks[i] === 'ok' ? 'ok' : 'err'">{{ checks[i] === 'ok' ? 'Looks good' : checks[i] }}</small>
      <button class="btn ghost small" @click="routines.splice(i, 1)">Remove</button>
    </div>
    <div class="actions"><button class="btn ghost" @click="addRow">Add routine</button><button class="btn" @click="save">Save routines</button></div>
  </section>`,
};

const HabitSettings = {
  setup() {
    const habits = ref([]), label = ref("");
    const load = async () => { habits.value = await api("/api/habits"); };
    onMounted(load);
    const add = async () => { if (!label.value.trim()) return; await api("/api/habits", { method: "POST", body: { label: label.value } }); label.value = ""; load(); };
    const del = async (h) => { await api(`/api/habits/${h.id}`, { method: "DELETE" }); load(); };
    return { habits, label, add, del };
  },
  template: `
  <section>
    <h2>Daily habits</h2>
    <p>Show up in Today's goals next to your training and nutrition.</p>
    <ul class="cal-list"><li v-for="h in habits" :key="h.id"><span>{{ h.label }}</span><button class="btn ghost small" @click="del(h)">Remove</button></li></ul>
    <form class="copy-row" @submit.prevent="add"><input type="text" v-model="label" placeholder="Read 20 pages, 2 L of water, stretch 10 min…" aria-label="New habit"><button class="btn">Add</button></form>
  </section>`,
};


const TodayView = {
  components: { WorkoutCard, LoadChart, FuelCard, Checklist },
  setup() {
    const data = ref(null), curve = ref(null), plan = ref(null), nut = ref(null), loading = ref(true), ck = ref(0), st = ref(null);
    const load = async () => {
      [data.value, curve.value, plan.value, nut.value, st.value] = await Promise.all([api("/api/today"), api("/api/load"), api("/api/plans/active"), api("/api/nutrition/day"), api("/api/study").catch(() => null)]);
      loading.value = false; ck.value++;
    };
    const fuelFor = (id) => nut.value?.sessions?.find((x) => x.session.id === id)?.fuel;
    onMounted(load);
    const lanes = computed(() => {
      const s = data.value?.week?.sports || {};
      return ["swim", "bike", "run", "gym"].filter((k) => s[k]).map((k) => {
        const e = s[k];
        const useKm = k !== "gym" && e.planned_km > 0;
        const done = useKm ? e.done_km : e.done_sessions, total = useKm ? e.planned_km : e.sessions;
        const unit = k === "swim" ? ((v) => `${Math.round(v * 1000)} m`) : useKm ? ((v) => `${v.toFixed(1)} km`) : ((v) => `${v}`);
        return { k, pct: Math.min(100, (done / (total || 1)) * 100), label: useKm ? `${unit(done)} of ${unit(total)}` : `${done} of ${total} sessions`, minutes: `${fmtMin(e.done_min)} of ${fmtMin(e.planned_min)}` };
      });
    });
    const notify = async () => {
      if (!("Notification" in window)) return toast("This browser doesn't support notifications.");
      const p = await Notification.requestPermission();
      if (p !== "granted") return toast("Notifications are blocked. Use the calendar feed in Settings for reminders.");
      const reg = await navigator.serviceWorker?.getRegistration();
      const n = nut.value;
      const body = [...data.value.messages, n && !n.setup_needed ? `Eat ${n.target.kcal} kcal today: ${n.target.protein_g} g protein, ${n.target.carbs_g} g carbs, ${n.target.fat_g} g fat.` : ""].filter(Boolean).join("\n");
      reg ? reg.showNotification("Taper", { body, icon: "icons/icon-192.png" }) : new Notification("Taper", { body });
    };
    const statusLabel = (s) => STATUS_LABEL[s] || s;
    const weekMeta = computed(() => plan.value?.weeks.find((w) => w.start === iso(mondayOf(parseISO(data.value.date)))));
    const soon = computed(() => {           // the next two exams and the next two other deadlines
      const all = (st.value?.deadlines || []).filter((d) => d.days_left <= 21);
      const pick = [...all.filter((d) => d.kind === "exam").slice(0, 2), ...all.filter((d) => d.kind !== "exam").slice(0, 2)];
      return all.filter((d) => pick.includes(d));
    });
    const studyToday = computed(() => (st.value?.blocks || []).filter((b) => b.day === data.value?.date));
    return { data, curve, plan, nut, ck, fuelFor, loading, load, lanes, notify, fmtDay, statusLabel, weekMeta, SPORTS, go, soon, studyToday, fmtH, leftLabel };
  },
  template: `
  <div v-if="loading" class="loading">Loading your day…</div>
  <div v-else>
    <div class="today-head">
      <div>
        <h1>{{ fmtDay(data.date) }}</h1>
        <p v-if="plan"><template v-if="weekMeta">Week {{ weekMeta.index + 1 }} of {{ plan.weeks.length }}, {{ weekMeta.phase }}{{ weekMeta.recovery ? ' (deload)' : '' }} · </template>{{ plan.goal.event_name }}{{ plan.goal.event !== 'general' ? ' on ' + fmtDay(plan.goal.race_date, { month: 'long', day: 'numeric', year: 'numeric' }) : '' }}</p>
      </div>
      <div v-if="data.days_to_race != null && data.days_to_race >= 0" class="countdown"><span class="num">{{ data.days_to_race }}</span><span class="muted">days to race day</span></div>
    </div>

    <div v-if="soon.length" class="soon">
      <a v-for="d in soon" :key="d.id" href="#/study" class="soon-item" :class="['k-' + d.kind, { hot: d.days_left <= 3 }]">
        <span class="num">{{ d.days_left }}</span><span><b>{{ d.course }}</b><small>{{ d.title }} · {{ leftLabel(d.days_left) }}</small></span>
      </a>
      <span class="soon-sum muted" v-if="studyToday.length">{{ fmtH(studyToday.reduce((a, b) => a + b.minutes, 0)) }} of study today</span>
    </div>

    <Checklist v-if="!plan" :day="data.date" :key="'np' + ck" @changed="load" style="max-width:620px" />
    <div v-if="!plan" class="empty-state">
      <h2>Set your goal to get a plan</h2>
      <p>Choose a race and a date, tell us how much time you have each day, and every session through race day gets scheduled.</p>
      <button class="btn" @click="go('/new-plan')">Set a goal</button>
    </div>

    <div v-else class="grid-today">
      <aside>
        <div class="bib" :class="'st-' + curve.readiness.status">
          <span class="pin-b"></span>
          <div class="event">Readiness</div>
          <div class="score num">{{ curve.readiness.score }}</div>
          <div class="status">{{ statusLabel(curve.readiness.status) }}</div>
          <p class="msg">{{ curve.readiness.message }}</p>
          <dl>
            <div><dt>Fitness</dt><dd>{{ curve.today.ctl.toFixed(0) }}</dd></div>
            <div><dt>Fatigue</dt><dd>{{ curve.today.atl.toFixed(0) }}</dd></div>
            <div><dt>Form</dt><dd>{{ curve.today.tsb > 0 ? '+' : '' }}{{ curve.today.tsb.toFixed(0) }}</dd></div>
          </dl>
        </div>
        <FuelCard v-if="nut" :n="nut" />
        <button class="btn ghost small" style="margin-top:12px" @click="notify">Send today's plan to my phone</button>
      </aside>

      <section>
        <p v-for="(c, i) in (data.level_changes || []).filter(c => c.stage_changed)" :key="'lv' + i" class="notice level-up">{{ SPORTS[c.sport] }}: {{ c.detail }}</p>
        <Checklist :day="data.date" :key="ck" @changed="load" />
        <div v-if="!data.sessions.length" class="rest-day">
          <h2>Rest day</h2>
          <p class="muted">Nothing scheduled. Recovery is when the training turns into fitness.</p>
        </div>
        <WorkoutCard v-for="s in data.sessions" :key="s.id" :s="s" :fuel="fuelFor(s.id)" @changed="load" />
      </section>
    </div>

    <template v-if="plan">
      <div class="section-h"><h2>This week's targets</h2><a href="#/week" class="muted">Open the week</a></div>
      <div class="lanes">
        <div v-for="l in lanes" :key="l.k" class="lane" :class="'s-' + l.k">
          <span class="name"><i class="dot"></i>{{ SPORTS[l.k] }}</span>
          <span class="track"><i :style="{ width: l.pct + '%' }"></i></span>
          <span class="fig"><b>{{ l.label }}</b><br>{{ l.minutes }}</span>
        </div>
      </div>

      <div class="section-h"><h2>Fitness, fatigue and form</h2><span class="muted">7-day ramp {{ curve.today.ramp_7d > 0 ? '+' : '' }}{{ curve.today.ramp_7d }} CTL</span></div>
      <LoadChart :points="curve.points" />
    </template>
  </div>`,
};

const WeekView = {
  components: { WorkoutCard },
  setup() {
    const start = ref(mondayOf(route.query.d ? parseISO(route.query.d) : new Date()));
    const sessions = ref([]), plan = ref(null), selected = ref(null), loading = ref(true), blocks = ref([]);
    const load = async () => {
      const s = iso(start.value), e = iso(addDays(start.value, 6));
      [sessions.value, plan.value, blocks.value] = await Promise.all([api(`/api/sessions?start=${s}&end=${e}`), plan.value ? plan.value : api("/api/plans/active"),
        api(`/api/study/blocks?start=${s}&end=${e}`).catch(() => [])]);
      if (selected.value) selected.value = sessions.value.find((x) => x.id === selected.value.id) || null;
      loading.value = false;
    };
    watch(start, load); onMounted(load);
    const days = computed(() => [...Array(7)].map((_, i) => {
      const d = iso(addDays(start.value, i));
      const items = [...sessions.value.filter((s) => s.day === d), ...blocks.value.filter((b) => b.day === d).map((b) => ({ ...b, study: true }))]
        .sort((a, b) => (a.start_time || "99").localeCompare(b.start_time || "99"));
      return { d, dow: DOW[i], date: parseISO(d).getDate(), items, today: d === iso(new Date()) };
    }));
    const meta = computed(() => plan.value?.weeks.find((w) => w.start === iso(start.value)));
    const totals = computed(() => {
      const live = sessions.value.filter((s) => s.status !== "missed");
      return {
        min: live.reduce((a, s) => a + s.minutes, 0),
        tss: live.filter((s) => s.sport !== "gym").reduce((a, s) => a + s.tss, 0),
        gymTss: live.filter((s) => s.sport === "gym").reduce((a, s) => a + s.tss, 0),
        done: sessions.value.filter((s) => s.status === "done").length, n: live.length,
      };
    });
    const shift = (n) => { start.value = addDays(start.value, 7 * n); history.replaceState(null, "", `#/week?d=${iso(start.value)}`); };
    const thisWeek = () => { start.value = mondayOf(new Date()); history.replaceState(null, "", "#/week"); };
    const changed = async () => { await load(); };   // the open card already lists any rebalancing
    const studyMin = computed(() => blocks.value.reduce((a, b) => a + b.minutes, 0));
    return { start, days, meta, totals, shift, thisWeek, selected, load, changed, loading, fmtMin, fmtDay, iso, studyMin };
  },
  template: `
  <div>
    <div class="today-head">
      <div>
        <h1>Week of {{ fmtDay(start, { month: 'long', day: 'numeric' }) }}</h1>
        <p v-if="meta">Week {{ meta.index + 1 }} <span class="phase-pill" :class="'ph-' + meta.phase">{{ meta.phase }}{{ meta.recovery ? ' · deload' : '' }}</span></p>
      </div>
      <div class="week-nav">
        <button class="btn ghost small" @click="shift(-1)" aria-label="Previous week">Previous</button>
        <button class="btn ghost small" @click="thisWeek">This week</button>
        <button class="btn ghost small" @click="shift(1)" aria-label="Next week">Next</button>
      </div>
    </div>
    <div class="week-summary">
      <div><span class="num">{{ fmtMin(totals.min) }}</span><small>planned time</small></div>
      <div><span class="num">{{ Math.round(totals.tss) }}</span><small>endurance load (TSS){{ meta ? ', target ' + meta.target_tss : '' }}{{ totals.gymTss ? ' · + ' + Math.round(totals.gymTss) + ' from strength' : '' }}</small></div>
      <div><span class="num">{{ totals.done }}/{{ totals.n }}</span><small>sessions done</small></div>
      <div v-if="studyMin"><span class="num">{{ fmtMin(studyMin) }}</span><small>of study planned</small></div>
    </div>
    <p v-if="meta && meta.notes.length" class="notice">{{ meta.notes.join(' ') }}</p>
    <div class="days" style="margin-top:16px">
      <div v-for="d in days" :key="d.d" class="day" :class="{ 'is-today': d.today }">
        <h3>{{ d.dow }} <small>{{ d.date }}</small></h3>
        <template v-for="s in d.items" :key="(s.study ? 'b' : 's') + s.id">
          <a v-if="s.study" href="#/study" class="tile s-study" :class="s.status">
            <span class="t">📖 {{ s.course }}</span>
            <span class="m">{{ s.start_time }}–{{ s.end_time }}</span>
          </a>
          <button v-else class="tile" :class="['s-' + s.sport, s.status, { moved: s.source === 'rebalance' }]" @click="selected = s">
            <span class="t">{{ s.title }}</span>
            <span class="m">{{ s.start_time ? s.start_time + ' · ' : '' }}{{ fmtMin(s.minutes) }} · {{ Math.round(s.tss) }} TSS</span>
          </button>
        </template>
        <span v-if="!d.items.length" class="empty">Rest</span>
      </div>
    </div>
    <div v-if="selected" class="scrim" @click.self="selected = null" @keydown.esc="selected = null">
      <div class="drawer" role="dialog" aria-modal="true">
        <button class="btn ghost small close" @click="selected = null">Close</button>
        <WorkoutCard :s="selected" :key="selected.id" show-day @changed="changed" />
      </div>
    </div>
  </div>`,
};


/* ================================================================ levels */
const LevelCheckForm = {
  props: { sport: String, sessionId: Number },
  emits: ["placed"],
  setup(props, { emit }) {
    const f = reactive({ minutes: props.sport === "bike" ? 30 : 20, continuous_min: null, distance_km: null, continuous_m: null,
      total_m: null, total_time: "", comfortable: true, avg_hr: null, avg_power: null, rpe: 6, months_lifting: null });
    const result = ref(null), busy = ref(false);
    const submit = async () => {
      busy.value = true;
      const body = { session_id: props.sessionId || null, rpe: f.rpe };
      if (props.sport === "run") Object.assign(body, { minutes: f.minutes, continuous_min: f.continuous_min, distance_km: f.distance_km || null, avg_hr: f.avg_hr || null });
      if (props.sport === "swim") {
        const [mm, ss] = String(f.total_time || "").split(":").map(Number);
        Object.assign(body, { continuous_m: f.continuous_m, total_m: f.total_m || null, total_sec: f.total_time ? mm * 60 + (ss || 0) : null });
      }
      if (props.sport === "bike") Object.assign(body, { minutes: f.minutes, distance_km: f.distance_km || null, comfortable: f.comfortable, avg_power: f.avg_power || null });
      if (props.sport === "gym") Object.assign(body, { months_lifting: f.months_lifting });
      try { result.value = await api(`/api/levels/${props.sport}/assess`, { method: "POST", body }); emit("placed", result.value); }
      catch (e) { toast(e.message); }
      busy.value = false;
    };
    return { f, result, busy, submit };
  },
  template: `
  <div class="levelcheck">
    <div v-if="result" class="placed">
      <h3>You're at: {{ result.name }}</h3>
      <p>{{ result.description }}</p>
      <ul><li v-for="r in result.reasons" :key="r">{{ r }}</li></ul>
      <p class="muted small">{{ result.replanned ? 'Your plan was rebuilt from this level.' : '' }}{{ Object.keys(result.thresholds).length ? ' Training zones updated from this check.' : '' }}</p>
    </div>
    <form v-else class="form-grid" @submit.prevent="submit">
      <template v-if="sport === 'run'">
        <label class="field">Minutes run before your first walk<input type="number" min="0" max="60" step="0.5" v-model.number="f.continuous_min" required></label>
        <label class="field">Total distance (km)<input type="number" min="0" step="0.01" v-model.number="f.distance_km" placeholder="from a watch or app"></label>
        <label class="field">Average heart rate (optional)<input type="number" min="60" max="220" v-model.number="f.avg_hr"></label>
        <label class="field">Total minutes<input type="number" min="5" max="60" v-model.number="f.minutes"></label>
      </template>
      <template v-if="sport === 'swim'">
        <label class="field">Metres before your first stop<input type="number" min="0" max="2000" step="5" v-model.number="f.continuous_m" required></label>
        <label class="field">Total metres swum<input type="number" min="0" max="2000" step="5" v-model.number="f.total_m"></label>
        <label class="field">Total time (mm:ss)<input type="text" v-model="f.total_time" pattern="\\d{1,2}:\\d{2}" placeholder="8:30"></label>
      </template>
      <template v-if="sport === 'bike'">
        <label class="field">Minutes ridden<input type="number" min="5" max="180" v-model.number="f.minutes" required></label>
        <label class="field">Distance (km)<input type="number" min="0" step="0.1" v-model.number="f.distance_km"></label>
        <label class="field">Average power (W, optional)<input type="number" min="0" v-model.number="f.avg_power"></label>
        <label class="field check"><input type="checkbox" v-model="f.comfortable"> I finished comfortably</label>
      </template>
      <template v-if="sport === 'gym'">
        <label class="field full">How many months have you lifted regularly (2+ times a week)?<input type="number" min="0" max="600" v-model.number="f.months_lifting" required></label>
      </template>
      <div v-if="sport !== 'gym'" class="field full">How hard did it feel? (1 = very easy, 10 = all-out)
        <div class="rpe-row"><button type="button" v-for="n in 10" :key="n" :class="{ on: f.rpe === n }" @click="f.rpe = n">{{ n }}</button></div>
      </div>
      <div class="full"><button class="btn" :disabled="busy">Find my level</button></div>
    </form>
  </div>`,
};

const LevelLadder = {
  components: { LevelCheckForm },
  props: { sport: String, data: Object },
  emits: ["changed"],
  setup(props, { emit }) {
    const checking = ref(false), picking = ref(false);
    const setStage = async (i) => { await api(`/api/levels/${props.sport}`, { method: "PUT", body: { stage: i } }); picking.value = false; toast("Level set. Plan rebuilt."); emit("changed"); };
    return { checking, picking, setStage, SPORTS, fmtDay };
  },
  template: `
  <section class="ladder" :class="'s-' + sport">
    <header>
      <h3><i class="dot"></i>{{ SPORTS[sport] }}</h3>
      <span v-if="data.projection && data.projection.required != null" :class="data.projection.on_track ? 'ok' : 'err'" class="small">
        {{ data.projection.weeks_to_required === 0 ? 'Goal level reached: ' + data.projection.required_name
          : (data.projection.on_track ? 'On track' : 'Behind') + ': goal needs ' + data.projection.required_name
            + (data.projection.weeks_to_required != null ? ' · about ' + data.projection.weeks_to_required + ' weeks away' : '') }}
      </span>
    </header>
    <div v-if="!data.assessed" class="needs-check">
      <p><b>Level not measured yet.</b> {{ data.check ? data.check.instructions : 'Answer one question to set your level.' }}</p>
      <p v-if="data.pending_check" class="muted small">Scheduled: {{ fmtDay(data.pending_check.day) }}{{ data.pending_check.start_time ? ' at ' + data.pending_check.start_time : '' }}. Do it, then log the result here.</p>
      <LevelCheckForm v-if="checking" :sport="sport" :session-id="data.pending_check ? data.pending_check.id : null" @placed="$emit('changed')" />
      <div v-else class="actions"><button class="btn small" @click="checking = true">{{ sport === 'gym' ? 'Answer' : 'Log my level check' }}</button><button class="btn ghost small" @click="picking = !picking">I know my level</button></div>
    </div>
    <ol class="rungs">
      <li v-for="st in data.ladder" :key="st.index" :class="{ now: st.index === data.stage, past: st.index < data.stage, goal: data.projection && st.index === data.projection.required }">
        <button v-if="picking" class="rung-pick" @click="setStage(st.index)">Choose</button>
        <span class="n num">{{ st.index + 1 }}</span>
        <span class="t"><b>{{ st.name }}</b><small>{{ st.index === data.stage ? st.description : st.criteria }}</small>
          <span v-if="st.index === data.stage && data.assessed" class="track"><i :style="{ width: Math.round(data.progress * 100) + '%' }"></i></span></span>
        <span class="tag" v-if="st.index === data.stage">You{{ data.assessed ? '' : ' (estimate)' }}</span>
        <span class="tag goal-tag" v-else-if="data.projection && st.index === data.projection.required">Goal</span>
      </li>
    </ol>
    <details v-if="data.history.length" class="hist-list"><summary>Recent reviews</summary>
      <ul><li v-for="(h, i) in data.history" :key="i"><span class="muted">{{ fmtDay(h.date, { month: 'short', day: 'numeric' }) }}</span> {{ h.detail }}</li></ul></details>
    <div v-if="data.assessed" class="actions"><button class="btn ghost small" @click="checking = !checking">Re-check my level</button><button class="btn ghost small" @click="picking = !picking">{{ picking ? 'Cancel' : 'Set it myself' }}</button></div>
    <LevelCheckForm v-if="checking && data.assessed" :sport="sport" @placed="$emit('changed')" />
  </section>`,
};

const LevelsPanel = {
  components: { LevelLadder },
  emits: ["changed"],
  setup(props, { emit }) {
    const d = ref(null);
    const load = async () => { d.value = await api("/api/levels"); };
    onMounted(load);
    const changed = async () => { await load(); emit("changed"); };
    return { d, changed };
  },
  template: `
  <div v-if="d">
    <div class="section-h" style="margin-top:8px"><h2>Your level in each sport</h2><span class="muted small">Reviewed every week from what you actually did</span></div>
    <p v-for="(c, i) in d.recent_changes.filter(c => c.stage_changed)" :key="i" class="notice">{{ c.detail }}</p>
    <div class="ladders"><LevelLadder v-for="(data, sport) in d.sports" :key="sport" :sport="sport" :data="data" @changed="changed" /></div>
  </div>`,
};

const SeasonView = {
  components: { SeasonStrip, LoadChart, LevelsPanel },
  setup() {
    const plan = ref(null), curve = ref(null), loading = ref(true);
    const reload = async () => { [plan.value, curve.value] = await Promise.all([api("/api/plans/active"), api("/api/load")]); loading.value = false; };
    onMounted(reload);
    const current = iso(mondayOf(new Date()));
    const notes = computed(() => [...new Set((plan.value?.weeks || []).flatMap((w) => w.notes))].slice(0, 5));
    const pick = (w) => go(`/week?d=${w.start}`);
    const hours = computed(() => plan.value ? Math.max(...plan.value.weeks.map((w) => w.minutes)) / 60 : 0);
    return { plan, curve, loading, current, notes, pick, hours, fmtDay, go, reload };
  },
  template: `
  <div v-if="loading" class="loading">Loading season…</div>
  <div v-else-if="!plan" class="empty-state"><h2>No goal yet</h2><p>Pick a race and a date to build your season.</p><button class="btn" @click="go('/new-plan')">Set a goal</button></div>
  <div v-else>
    <div class="season-head">
      <div>
        <h1>{{ plan.goal.event_name }}</h1>
        <p class="muted" style="margin:6px 0 0">{{ plan.goal.event === 'general' ? 'Ongoing block until ' : 'Race day ' }}{{ fmtDay(plan.goal.race_date, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' }) }} · {{ plan.weeks.length }} weeks · {{ plan.goal.level }}</p>
      </div>
      <button class="btn ghost" @click="go('/new-plan')">Change goal</button>
    </div>
    <LevelsPanel @changed="reload" />
    <div class="proj">
      <div><span class="num">{{ plan.projection.start_ctl }} → {{ plan.projection.race_day_ctl }}</span><small>fitness (CTL) from start to race day</small></div>
      <div><span class="num">{{ plan.projection.race_day_tsb > 0 ? '+' : '' }}{{ plan.projection.race_day_tsb }}</span><small>form on race day (aim for +5 to +25)</small></div>
      <div><span class="num">{{ plan.projection.max_ramp }}</span><small>steepest 7-day fitness ramp</small></div>
      <div><span class="num">{{ hours.toFixed(1) }} h</span><small>biggest training week</small></div>
    </div>
    <div class="strip-wrap">
      <SeasonStrip :weeks="plan.weeks" :current="current" @pick="pick" />
      <div class="strip-legend">
        <span><i class="ph-base"></i>Base: aerobic volume</span><span><i class="ph-build"></i>Build: threshold work</span>
        <span><i class="ph-peak"></i>Peak: race-specific</span><span><i class="ph-taper"></i>Taper: less volume, same intensity</span>
        <span><i class="rec"></i>Deload week</span><span>Bar height is weekly training load. Pick a week to open it.</span>
      </div>
    </div>
    <ul v-if="notes.length" class="notes-list"><li v-for="n in notes" :key="n">{{ n }}</li></ul>
    <div class="section-h"><h2>Season load curve</h2></div>
    <LoadChart :points="curve.points" :height="300" />
  </div>`,
};

const PICKS = [["run", "Running"], ["bike", "Cycling"], ["swim", "Swimming"], ["gym", "Strength"], ["walk", "Walking"],
  ["mobility", "Mobility"]];
const PICK_HINT = { mobility: "Stretching, balance, core. 20 min, no equipment" };
const EQUIP = [["gym", "A gym"], ["home", "At home", "Dumbbells, bands"], ["none", "No equipment"]];
const BODY = [["lose", "Lose fat", "Eat a little less, keep the muscle"], ["recomp", "Recomposition", "Lose fat and build muscle slowly"],
  ["maintain", "Stay as I am", "Eat to fuel training"], ["gain", "Build muscle", "Eat a little more"]];

const NewPlanView = {
  components: { SeasonStrip },
  setup() {
    const step = ref(1), events = ref([]), me = ref(null), cal = ref([]), preview = ref(null), err = ref(""), busy = ref(false);
    const g = reactive({ sports: [], race: null, race_date: iso(addDays(new Date(), 7 * 16)), target_time: "", block_weeks: 12,
      mode: "recomp", goal_weight: null, goal_date: "", today_weight: null, hours: 3, gym_per_week: 2,
      equipment: "gym", away_days: [], commitments: [], other: false,
      availability: { 0: 60, 1: 60, 2: 60, 3: 60, 4: 0, 5: 120, 6: 120 } });
    onMounted(async () => {
      [events.value, me.value, cal.value] = await Promise.all([api("/api/events"), api("/api/me"), api("/api/calendars")]);
      Object.assign(g.availability, me.value.availability);
      const n = await api("/api/nutrition/day").catch(() => null);
      g.known_weight = n && !n.setup_needed ? n.expenditure.weight_kg : null;
    });
    const toggle = (k) => { g.sports = g.sports.includes(k) ? g.sports.filter((x) => x !== k) : [...g.sports, k]; g.race = null; };
    // only races the picked sports can do (a triathlon needs swim + bike + run)
    const races = computed(() => events.value.filter((e) => e.sports.every((s) => g.sports.includes(s))));
    const race = computed(() => events.value.find((e) => e.key === g.race));
    const level = computed(() => (g.hours < 2 ? "beginner" : g.hours < 6 ? "intermediate" : "advanced"));
    const body = () => ({
      event: g.race || "custom", sports: g.sports, race_date: g.race ? g.race_date : null,
      block_weeks: g.race ? null : g.block_weeks, target_time: g.race && race.value?.target_time && g.target_time ? g.target_time : null,
      body_focus: !g.race && g.mode !== "maintain", level: level.value, baseline_hours: g.hours,
      gym_per_week: g.sports.includes("gym") ? g.gym_per_week : 0, availability: g.availability,
      equipment: g.sports.includes("gym") ? g.equipment : "gym", away_days: g.sports.includes("gym") ? g.away_days : [],
      commitments: g.other ? g.commitments.filter((c) => c.name && c.minutes) : [],
    });
    const toggleAway = (i) => { g.away_days = g.away_days.includes(i) ? g.away_days.filter((x) => x !== i) : [...g.away_days, i]; };
    const addOther = () => { g.other = true; g.commitments.push({ name: "", day: 1, minutes: 90, hard: true }); };
    const canNext = computed(() => ({ 1: g.sports.length > 0, 2: g.race !== null,
      3: !["lose", "gain"].includes(g.mode) || g.goal_weight, 4: true }[step.value] ?? true));
    const next = async () => {
      err.value = "";
      if (step.value === 4) {
        busy.value = true;
        try { preview.value = await api("/api/plans/preview", { method: "POST", body: body() }); step.value = 5; }
        catch (e) { err.value = e.message; }
        busy.value = false; return;
      }
      step.value++;
    };
    const create = async () => {
      busy.value = true;
      try {
        if (g.today_weight) await api("/api/weights", { method: "PUT", body: { day: iso(new Date()), kg: +g.today_weight } });
        await api("/api/settings", { method: "PUT", body: { nutrition: { mode: g.mode,
          goal_weight_kg: ["lose", "gain"].includes(g.mode) ? +g.goal_weight : null,
          goal_date: ["lose", "gain"].includes(g.mode) ? (g.goal_date || (g.race ? g.race_date : iso(addDays(new Date(), 7 * g.block_weeks)))) : null } } });
        await api("/api/plans", { method: "POST", body: body() });
        toast("Your plan is ready."); go("/today");
      } catch (e) { err.value = e.message; }
      busy.value = false;
    };
    const sportName = (k) => PICKS.find((p) => p[0] === k)?.[1];
    return { step, g, events, races, race, cal, preview, err, busy, toggle, next, create, canNext, level, PICKS, PICK_HINT, BODY, DOW,
      EQUIP, toggleAway, addOther, fmtMin, fmtDay, sportName, SPORTS };
  },
  template: `
  <div class="wiz">
    <ol class="wiz-steps" aria-label="Progress"><li v-for="n in 5" :key="n" :class="{ on: step === n, done: step > n }"></li></ol>

    <section v-if="step === 1">
      <h1>What do you want to train?</h1>
      <p class="muted">Pick one or several. You can change this later.</p>
      <div class="pick-grid">
        <button v-for="[k, label] in PICKS" :key="k" class="pick" :class="['s-' + k, { on: g.sports.includes(k) }]" :aria-pressed="g.sports.includes(k)" @click="toggle(k)">
          <i class="dot"></i>{{ label }}<small v-if="PICK_HINT[k]">{{ PICK_HINT[k] }}</small></button>
      </div>
    </section>

    <section v-else-if="step === 2">
      <h1>Are you training for a race?</h1>
      <div class="choice-list">
        <button class="choice" :class="{ on: g.race === '' }" @click="g.race = ''"><b>No race, I just want to get better</b><small>A rolling block that adapts as you improve</small></button>
        <button v-for="e in races" :key="e.key" class="choice" :class="{ on: g.race === e.key }" @click="g.race = e.key"><b>{{ e.name }}</b></button>
      </div>
      <p v-if="!races.length" class="muted small">No races use only {{ g.sports.map(sportName).join(' and ').toLowerCase() }}: a rolling block is the way to go.</p>
      <div v-if="g.race" class="form-row" style="margin-top:18px">
        <label class="field">Race date<input type="date" v-model="g.race_date"></label>
        <label v-if="race && race.target_time" class="field">Goal time (optional)<input type="text" v-model="g.target_time" placeholder="1:45:00" pattern="\\d{1,2}:\\d{2}(:\\d{2})?"></label>
      </div>
      <div v-if="g.race === ''" class="chips" style="margin-top:18px">
        <span class="muted">Block length:</span>
        <button v-for="w in [8, 12, 16, 24]" :key="w" class="chip" :class="{ on: g.block_weeks === w }" @click="g.block_weeks = w">{{ w }} weeks</button>
      </div>
    </section>

    <section v-else-if="step === 3">
      <h1>And your body?</h1>
      <div class="choice-list two">
        <button v-for="[k, label, hint] in BODY" :key="k" class="choice" :class="{ on: g.mode === k }" @click="g.mode = k"><b>{{ label }}</b><small>{{ hint }}</small></button>
      </div>
      <div class="form-row" style="margin-top:18px">
        <label v-if="!g.known_weight" class="field">Today's weight (kg)<input type="number" step="0.1" min="25" max="350" v-model="g.today_weight"></label>
        <template v-if="['lose', 'gain'].includes(g.mode)">
          <label class="field">Goal weight (kg)<input type="number" step="0.5" min="30" max="300" v-model="g.goal_weight" required></label>
          <label class="field">By (optional)<input type="date" v-model="g.goal_date"></label>
        </template>
      </div>
    </section>

    <section v-else-if="step === 4">
      <h1>When can you train?</h1>
      <p v-if="cal.length" class="notice">Taper will use your Google Calendar to find free time. The minutes below are only a fallback.</p>
      <div v-for="(d, i) in DOW" :key="d" class="avail">
        <label :for="'av' + i">{{ d }}</label>
        <input :id="'av' + i" type="range" min="0" max="240" step="15" v-model.number="g.availability[i]">
        <output>{{ g.availability[i] ? fmtMin(g.availability[i]) : 'Off' }}</output>
      </div>
      <div class="form-row" style="margin-top:18px">
        <label class="field">Hours a week you train now<input type="number" min="0" max="25" step="0.5" v-model.number="g.hours"></label>
        <label v-if="g.sports.includes('gym')" class="field">Strength sessions a week<input type="number" min="1" max="6" v-model.number="g.gym_per_week"></label>
      </div>
      <template v-if="g.sports.includes('gym')">
        <h3 class="wiz-sub">Where do you do strength?</h3>
        <div class="choice-list three">
          <button v-for="[k, label, hint] in EQUIP" :key="k" class="choice" :class="{ on: g.equipment === k }" @click="g.equipment = k"><b>{{ label }}</b><small v-if="hint">{{ hint }}</small></button>
        </div>
        <div v-if="g.equipment !== 'none'" class="chips" style="margin-top:10px">
          <span class="muted small">Away from it some days (travel)?</span>
          <button v-for="(d, i) in DOW" :key="d" class="chip" :class="{ on: g.away_days.includes(i) }" :aria-pressed="g.away_days.includes(i)" @click="toggleAway(i)">{{ d }}</button>
        </div>
      </template>
      <h3 class="wiz-sub">Do you already train somewhere else?</h3>
      <p v-if="!g.other" class="muted small">A club practice, climbing, a team sport… <button class="link" @click="addOther">Add it</button> and Taper plans around it.</p>
      <div v-else class="others">
        <div v-for="(c, i) in g.commitments" :key="i" class="other-row">
          <input type="text" v-model="c.name" placeholder="Kickboxing" maxlength="40" aria-label="What">
          <select v-model.number="c.day" aria-label="Day"><option v-for="(d, j) in DOW" :key="d" :value="j">{{ d }}</option></select>
          <label class="mins"><input type="number" min="10" max="300" step="5" v-model.number="c.minutes" aria-label="Minutes"> min</label>
          <label class="hard"><input type="checkbox" v-model="c.hard"> Hard</label>
          <button class="link" @click="g.commitments.splice(i, 1); if (!g.commitments.length) g.other = false" aria-label="Remove">✕</button>
        </div>
        <button class="link" @click="addOther">+ Another day</button>
      </div>
      <p class="muted small">You'll do a short level check in each sport in week 1, so you don't need to know your level.</p>
    </section>

    <section v-else-if="step === 5 && preview">
      <h1>{{ preview.projection.event_name }}</h1>
      <p class="muted">{{ preview.weeks.length }} weeks{{ g.race ? ', race on ' + fmtDay(g.race_date, { month: 'long', day: 'numeric' }) : '' }}. Starts at your level and builds from there.</p>
      <SeasonStrip :weeks="preview.weeks" compact />
      <ul class="lv-summary">
        <li v-for="(v, k) in preview.projection.levels" :key="k" :class="'s-' + k"><i class="dot"></i><b>{{ SPORTS[k] }}</b>
          {{ v.assessed ? v.now_name : (k === 'gym' ? 'one question on the Progress tab' : 'level check in week 1') }} → {{ v.at_goal_name }}{{ v.required_name ? (v.on_track ? ' ✓' : ' (goal needs ' + v.required_name + ')') : '' }}</li>
      </ul>
      <p v-if="preview.projection.target" class="notice">Goal pace {{ Math.floor(preview.projection.target.race_pace / 60) }}:{{ String(preview.projection.target.race_pace % 60).padStart(2, '0') }}/km.
        {{ !preview.projection.target.measured ? "After your run level check, Taper will tell you if it's realistic." :
           preview.projection.target.realistic ? 'Looks realistic from your current fitness.' : 'Ambitious: about ' + preview.projection.target.weeks_needed + ' weeks of improvement needed.' }}</p>
      <p v-if="preview.commitments.length" class="notice">Your {{ [...new Set(preview.commitments.map((c) => c.name))].join(' and ') }} days stay free of hard sessions, with no heavy leg work the day before.</p>
      <p v-if="preview.easy_days" class="notice">{{ preview.easy_days }} days after night shifts are kept easy: no intervals, no heavy lifting.</p>
      <h3 style="margin-top:18px">Your first weeks</h3>
      <ul class="sample"><li v-for="(s, i) in preview.sample.slice(0, 10)" :key="i" :class="'s-' + s.sport"><span>{{ fmtDay(s.day, { weekday: 'short', day: 'numeric' }) }}</span><b>{{ s.title }}</b><span class="muted">{{ s.minutes }} min</span></li></ul>
    </section>

    <p v-if="err" class="err">{{ err }}</p>
    <div class="wiz-nav">
      <button v-if="step > 1" class="btn ghost" @click="step--">Back</button>
      <button v-if="step < 5" class="btn" :disabled="!canNext || busy" @click="next">{{ step === 4 ? (busy ? 'Building…' : 'See my plan') : 'Next' }}</button>
      <button v-else class="btn" :disabled="busy" @click="create">{{ busy ? 'Saving…' : 'Start this plan' }}</button>
    </div>
  </div>`,
};

const EXAMPLES = [
  "Run: Warmup 15m @ Z2 -> 5x(1km @ 4:15/km, Rest 90s) -> Cooldown 10m",
  "Swim: 400m Easy -> 8x100m Freestyle @ 1:45/100m (20s rest) -> 200m Pull Buoy",
  "Bike: Warmup 15min @ Z2 -> 3x(12min @ 90%FTP, Rest 5min) -> Cooldown 10min",
  "Gym: Back Squat 4x6 @ RPE 8 | Superset: Romanian Deadlift 3x10 + Hanging Leg Raises 3x15",
];

const BuilderView = {
  components: { WorkoutSteps, Profile },
  setup() {
    const text = ref(EXAMPLES[0]), res = ref(null), day = ref(iso(new Date())), title = ref("");
    let t;
    watch(text, () => { clearTimeout(t); t = setTimeout(parse, 180); }, { immediate: true });
    async function parse() { res.value = await api("/api/dsl/parse", { method: "POST", body: { text: text.value } }); }
    const add = async () => {
      try { await api("/api/sessions", { method: "POST", body: { day: day.value, dsl: text.value, title: title.value || null } }); toast(`Added to ${fmtDay(day.value)}`); }
      catch (e) { toast(e.message); }
    };
    const profile = computed(() => res.value?.estimate?.breakdown.map((b) => [b.duration_s, b.if]) || []);
    return { text, res, day, title, add, EXAMPLES, profile, fmtMin, fmtDist };
  },
  template: `
  <div>
    <h1 style="margin-bottom:6px">Write a workout</h1>
    <p class="muted" style="margin:0 0 22px">Type it the way you'd write it on a whiteboard. It's checked as you type and turned into a structured session with time, distance and load.</p>
    <div class="builder">
      <div>
        <label class="field" for="dsl">Workout</label>
        <textarea id="dsl" v-model="text" rows="4" spellcheck="false"></textarea>
        <div class="examples"><button v-for="e in EXAMPLES" :key="e" @click="text = e">{{ e.split(':')[0] }} example</button></div>
        <div class="form-row" style="margin-top:16px">
          <label class="field">Name (optional)<input type="text" v-model="title" placeholder="Tuesday track session"></label>
          <label class="field">Date<input type="date" v-model="day"></label>
        </div>
        <button class="btn" :disabled="!res || !res.ok" @click="add">Add to my calendar</button>
        <div class="syntax">
          <h3 style="color:var(--ink)">Syntax</h3>
          <table>
            <tr><td><code>-&gt;</code></td><td>Next step (run, bike, swim)</td></tr>
            <tr><td><code>5x( … , … )</code></td><td>Repeat a group of steps</td></tr>
            <tr><td><code>8x100m … (20s rest)</code></td><td>Shorthand repeat with rest</td></tr>
            <tr><td><code>@ Z1–Z5</code>, <code>@ 4:15/km</code>, <code>@ 1:45/100m</code>, <code>@ 250W</code>, <code>@ 90%FTP</code>, <code>@ RPE 7</code></td><td>Targets</td></tr>
            <tr><td><code>s</code>, <code>min</code>, <code>h</code>, <code>m</code>, <code>km</code>, <code>mi</code></td><td>Units. A bare <code>m</code> on a run means minutes when under 100</td></tr>
            <tr><td><code>|</code>, <code>Superset: A + B</code></td><td>Gym blocks and supersets</td></tr>
          </table>
        </div>
      </div>
      <div>
        <p v-if="res && !res.ok" class="err notice" style="border-color:var(--bad)">{{ res.error }}</p>
        <article v-else-if="res" class="workout" :class="'s-' + res.workout.sport">
          <h2>{{ title || 'Preview' }}</h2>
          <p class="meta"><span><b>{{ fmtMin(res.estimate.minutes) }}</b></span>
            <span v-if="res.estimate.distance_m"><b>{{ fmtDist(res.estimate.distance_m, res.workout.sport) }}</b></span>
            <span><b>{{ Math.round(res.estimate.tss) }}</b> TSS</span><span>IF {{ res.estimate.if }}</span></p>
          <Profile v-if="profile.length > 1" :profile="profile" />
          <WorkoutSteps :steps="res.workout.steps" />
        </article>
      </div>
    </div>
  </div>`,
};

const AppearanceSettings = {
  setup: () => ({ theme, setTheme }),
  template: `
  <section>
    <h2>Appearance</h2>
    <p>Dark is easier on the eyes for early runs and late study nights.</p>
    <div class="theme-pick" role="radiogroup" aria-label="Theme">
      <label><input type="radio" name="theme" value="light" :checked="theme.choice === 'light'" @change="setTheme('light')"> Light</label>
      <label><input type="radio" name="theme" value="dark" :checked="theme.choice === 'dark'" @change="setTheme('dark')"> Dark</label>
      <label><input type="radio" name="theme" value="system" :checked="theme.choice === 'system'" @change="setTheme('system')"> Match my device</label>
    </div>
  </section>`,
};

const SettingsView = {
  components: { CalendarSettings, RoutineSettings, HabitSettings, DataSettings, AppearanceSettings },
  setup() {
    const me = ref(null), strava = ref(null), saved = ref(false);
    const th = reactive({ runPace: "5:00", css: "2:00", ftp: 200, kmh: 32 });
    const toSec = (s) => { const [m, x] = String(s).split(":").map(Number); return m * 60 + (x || 0); };
    const toClock = (v) => `${Math.floor(v / 60)}:${String(Math.round(v % 60)).padStart(2, "0")}`;
    onMounted(async () => {
      me.value = await api("/api/me");
      const t = me.value.thresholds;
      Object.assign(th, { runPace: toClock(t.run_threshold_pace), css: toClock(t.swim_css), ftp: t.bike_ftp, kmh: t.bike_threshold_kmh });
      strava.value = await api("/api/strava/status").catch(() => null);
      if (route.query.strava) toast("Strava connected. New activities will tick off your plan.");
    });
    const save = async () => {
      me.value = await api("/api/me", { method: "PUT", body: { thresholds: {
        run_threshold_pace: toSec(th.runPace), swim_css: toSec(th.css), bike_ftp: th.ftp, bike_threshold_kmh: th.kmh } } });
      toast("Thresholds saved. New and edited workouts use them.");
    };
    const feed = computed(() => me.value ? `${location.origin}/api/calendar/${me.value.ics_token}.ics` : "");
    const copy = async () => { await navigator.clipboard.writeText(feed.value); toast("Calendar link copied"); };
    const rotate = async () => { const r = await api("/api/me/ics-token", { method: "POST" }); me.value.ics_token = r.ics_token; toast("New link created. The old one stops working."); };
    const connect = async () => { try { location.href = (await api("/api/strava/connect")).url; } catch (e) { toast(e.message); } };
    const disconnect = async () => { await api("/api/strava", { method: "DELETE" }); strava.value.connected = false; toast("Strava disconnected"); };
    return { me, th, save, feed, copy, rotate, strava, connect, disconnect, logout };
  },
  template: `
  <div v-if="me" class="settings">
    <h1>Settings</h1>
    <section>
      <h2>Thresholds</h2>
      <p>These turn zones and %FTP into real paces and set how much stress each workout counts for.</p>
      <form class="form-row" @submit.prevent="save">
        <label class="field">Run threshold pace (min/km)<input type="text" v-model="th.runPace" pattern="\\d+:\\d{2}" placeholder="4:45"></label>
        <label class="field">Swim CSS (min/100 m)<input type="text" v-model="th.css" pattern="\\d+:\\d{2}" placeholder="1:50"></label>
        <label class="field">Bike FTP (watts)<input type="number" v-model.number="th.ftp" min="50" max="600"></label>
        <label class="field">Speed at FTP (km/h)<input type="number" v-model.number="th.kmh" min="15" max="55"></label>
        <div><button class="btn">Save thresholds</button></div>
      </form>
    </section>
    <AppearanceSettings />
    <CalendarSettings />
    <RoutineSettings />
    <HabitSettings />
    <DataSettings />
    <section>
      <h2>Calendar feed and reminders</h2>
      <p>Subscribe to this link in Google Calendar, Apple Calendar or Outlook. Every workout shows up with its full steps and a morning reminder, and the calendar refreshes when your plan changes.</p>
      <div class="copy-row"><input type="text" readonly :value="feed" aria-label="Calendar feed link"><button class="btn" @click="copy">Copy link</button></div>
      <p style="margin-top:10px"><button class="btn ghost small" @click="rotate">Create a new private link</button></p>
    </section>
    <section>
      <h2>Strava</h2>
      <template v-if="strava && strava.enabled">
        <p v-if="strava.connected">Connected. Runs, rides and swims you record are matched to the planned session for that day and logged automatically.</p>
        <p v-else>Connect Strava so finished activities tick off your plan and update your fitness curve.</p>
        <button v-if="strava.connected" class="btn ghost" @click="disconnect">Disconnect Strava</button>
        <button v-else class="btn" @click="connect">Connect Strava</button>
      </template>
      <p v-else>Strava sync isn't set up on this server. Self-hosters can add <code>STRAVA_CLIENT_ID</code> and <code>STRAVA_CLIENT_SECRET</code> to enable it.</p>
    </section>
    <section>
      <h2>Account</h2>
      <p>Signed in as {{ me.email }}{{ me.is_demo ? ' (demo athlete; changes are shared with other visitors)' : '' }}.</p>
      <button class="btn ghost" @click="logout">Sign out</button>
    </section>
  </div>`,
};

/* ================================================================ study */
const KIND = { exam: "Exam", assignment: "Assignment", quiz: "Quiz" };
const fmtH = (min) => { const h = (min || 0) / 60; return `${h >= 10 ? Math.round(h) : Math.round(h * 2) / 2} h`; };
const fmtDue = (d) => `${fmtDay(d.due_date, { weekday: "short", month: "short", day: "numeric" })} · ${d.due_time === "23:59" ? "end of day" : d.due_time}`;
const leftLabel = (n) => n <= 0 ? "today" : n === 1 ? "tomorrow" : `in ${n} days`;

const DeadlineForm = {
  props: { d: Object },
  emits: ["saved", "cancel"],
  setup(props, { emit }) {
    const f = reactive(props.d ? { ...props.d } : { course: "", title: "", kind: "exam", due_date: iso(addDays(new Date(), 7)), due_time: "09:00", end_time: "", weight: null, hours: null, location: "", done: false });
    const busy = ref(false);
    const save = async () => {
      busy.value = true;
      const body = { course: f.course, title: f.title, kind: f.kind, due_date: f.due_date, due_time: f.due_time || "23:59",
        end_time: f.end_time || null, weight: f.weight === "" ? null : f.weight, hours: f.hours === "" ? null : f.hours,
        location: f.location || null, done: !!f.done };
      try {
        const r = await api(props.d ? `/api/study/deadlines/${props.d.id}` : "/api/study/deadlines", { method: props.d ? "PUT" : "POST", body });
        emit("saved", r.plan);
      } catch (e) { toast(e.message); }
      busy.value = false;
    };
    return { f, save, busy };
  },
  template: `
  <form class="dl-form" @submit.prevent="save">
    <div class="form-row">
      <label class="field">Course<input v-model="f.course" required placeholder="Bases de données"></label>
      <label class="field">What<input v-model="f.title" required placeholder="Examen final"></label>
      <label class="field">Type<select v-model="f.kind"><option value="exam">Exam</option><option value="assignment">Assignment / TP</option><option value="quiz">Quiz</option></select></label>
    </div>
    <div class="form-row">
      <label class="field">{{ f.kind === 'exam' ? 'Date' : 'Due date' }}<input type="date" v-model="f.due_date" required></label>
      <label class="field">{{ f.kind === 'exam' ? 'Starts' : 'Due at' }}<input type="time" v-model="f.due_time"></label>
      <label v-if="f.kind === 'exam'" class="field">Ends<input type="time" v-model="f.end_time"></label>
      <label class="field">Weight (%)<input type="number" min="0" max="100" step="0.5" v-model.number="f.weight"></label>
      <label class="field">Hours to put in<input type="number" min="0" max="200" step="0.5" v-model.number="f.hours" placeholder="auto"></label>
    </div>
    <div class="actions" style="margin-top:0"><button class="btn" :disabled="busy">{{ d ? 'Save' : 'Add' }}</button><button type="button" class="btn ghost" @click="$emit('cancel')">Cancel</button></div>
  </form>`,
};

const StudySettings = {
  props: { s: Object },
  emits: ["saved"],
  setup(props, { emit }) {
    const f = reactive({ ...props.s });
    const save = async () => { await api("/api/settings", { method: "PUT", body: { study: { ...f } } }); emit("saved"); };
    return { f, save };
  },
  template: `
  <form @submit.prevent="save">
    <div class="form-row">
      <label class="field">Block length (min)<input type="number" min="30" max="180" step="5" v-model.number="f.block_min"></label>
      <label class="field">Max study per day (h)<input type="number" min="1" max="14" step="0.5" v-model.number="f.max_per_day_h"></label>
      <label class="field">Earliest study<input type="time" v-model="f.day_start"></label>
      <label class="field">Latest finish<input type="time" v-model="f.day_end"></label>
      <label class="field">Stop before an exam (min)<input type="number" min="0" max="240" step="15" v-model.number="f.exam_gap_min"></label>
    </div>
    <label class="field">Calendar events you can study during (words from their titles, comma-separated; add :hours to cap it per event)<input v-model="f.ok_during" placeholder="RAMQ:4, bus"></label>
    <div class="actions"><button class="btn">Save and rebuild</button></div>
  </form>`,
};

const StudyView = {
  components: { DeadlineForm, StudySettings },
  setup() {
    const o = ref(null), editing = ref(null), adding = ref(false), busy = ref(false), paste = ref(""), showSettings = ref(false);
    const load = async () => { o.value = await api("/api/study"); };
    onMounted(load);
    const report = (p) => {
      if (!p) return;
      const short = (p.short || []).map((x) => `${x.course} ${x.title} (${fmtH(x.short_min)} short)`);
      toast(short.length ? `Not enough free time for: ${short.join(", ")}. Free up time or lower the hours.` : `Study plan rebuilt: ${p.blocks} blocks, ${fmtH(p.minutes)}.`);
    };
    const rebuild = async () => { busy.value = true; try { report(await api("/api/study/plan", { method: "POST" })); await load(); } catch (e) { toast(e.message); } busy.value = false; };
    const saved = async (p) => { editing.value = null; adding.value = false; report(p); await load(); };
    const del = async (d) => { if (!confirm(`Delete ${d.course} — ${d.title} and its study blocks?`)) return; report((await api(`/api/study/deadlines/${d.id}`, { method: "DELETE" })).plan); await load(); };
    const markDone = async (d) => { await api(`/api/study/deadlines/${d.id}`, { method: "PUT", body: { ...d, done: true } }); toast("Nice. Marked done, its future blocks are freed up."); await load(); };
    const imp = async () => {
      busy.value = true;
      try { const r = await api("/api/study/import", { method: "POST", body: { text: paste.value } }); paste.value = "";
        toast(`${r.added} added, ${r.updated} updated${r.errors.length ? ` · ${r.errors.join("; ")}` : ""}`); report(r.plan); await load(); }
      catch (e) { toast(e.message); }
      busy.value = false;
    };
    const setBlock = async (b, status) => { await api(`/api/study/blocks/${b.id}`, { method: "PATCH", body: { status } }); await load(); };
    const days = computed(() => {
      if (!o.value) return [];
      const m = {};
      for (const b of o.value.blocks) (m[b.day] ||= []).push(b);
      return Object.keys(m).sort().slice(0, 7).map((d) => ({ d, items: m[d], min: m[d].reduce((a, b) => a + b.minutes, 0),
        due: o.value.deadlines.filter((x) => x.due_date === d) }));
    });
    const next = computed(() => o.value?.deadlines.find((d) => d.kind === "exam"));
    const pct = (d) => Math.min(100, (d.done_min / Math.max(1, d.hours_needed * 60)) * 100);
    const pctPlan = (d) => Math.min(100 - pct(d), (d.planned_min / Math.max(1, d.hours_needed * 60)) * 100);
    return { o, editing, adding, busy, paste, showSettings, rebuild, saved, del, markDone, imp, setBlock, days, next, pct, pctPlan, fmtH, fmtDue, leftLabel, fmtDay, KIND, load, report };
  },
  template: `
  <div v-if="!o" class="loading">Loading your semester…</div>
  <div v-else class="study">
    <div class="today-head">
      <div>
        <h1>Study</h1>
        <p>Every deadline gets its hours, placed in your free time around classes, work and training. Closest and heaviest first.</p>
      </div>
      <div v-if="next" class="countdown"><span class="num">{{ next.days_left }}</span><span class="muted">days to {{ next.course }}</span></div>
    </div>

    <div v-if="!o.deadlines.length && !adding" class="empty-state">
      <h2>Add your exams and due dates</h2>
      <p>Paste them below (one per line) or add them one at a time. Taper estimates the hours from the weight and builds your revision blocks.</p>
    </div>

    <div class="actions">
      <button class="btn" @click="adding = !adding; editing = null">Add a deadline</button>
      <button class="btn ghost" :disabled="busy || !o.deadlines.length" @click="rebuild">Rebuild study plan</button>
      <button class="btn ghost" @click="showSettings = !showSettings">Study settings</button>
    </div>
    <section v-if="showSettings" class="card-sec"><StudySettings :s="o.settings" @saved="showSettings = false; rebuild()" /></section>
    <section v-if="adding" class="card-sec"><DeadlineForm @saved="saved" @cancel="adding = false" /></section>

    <div v-if="days.length" class="section-h"><h2>Next study days</h2><span class="muted">Tick blocks off on the Today page, or here</span></div>
    <div v-if="days.length" class="sdays">
      <div v-for="day in days" :key="day.d" class="sday">
        <h3>{{ fmtDay(day.d, { weekday: 'short', month: 'short', day: 'numeric' }) }} <small>{{ fmtH(day.min) }}</small></h3>
        <p v-for="x in day.due" :key="'d' + x.id" class="due-pill" :class="'k-' + x.kind">{{ x.kind === 'exam' ? '📝' : '⏰' }} {{ x.course }} {{ x.title }} · {{ x.due_time }}</p>
        <div v-for="b in day.items" :key="b.id" class="sblock" :class="b.status">
          <span class="time num">{{ b.start_time }}–{{ b.end_time }}</span>
          <span class="what"><b>{{ b.course }}</b><small>{{ b.title.split(' — ')[1] }}</small></span>
          <span class="bact">
            <button class="btn ghost small" :aria-pressed="b.status === 'done'" @click="setBlock(b, b.status === 'done' ? 'planned' : 'done')">{{ b.status === 'done' ? 'Undo' : 'Done' }}</button>
            <button v-if="b.status !== 'done'" class="btn ghost small" @click="setBlock(b, 'missed')" title="Skipped: rebuild to catch the hours up later">Skip</button>
          </span>
        </div>
      </div>
    </div>

    <div v-if="o.deadlines.length" class="section-h"><h2>Coming up</h2><span class="muted">{{ o.deadlines.length }} deadlines</span></div>
    <div class="deadlines">
      <article v-for="d in o.deadlines" :key="d.id" class="deadline" :class="'k-' + d.kind">
        <template v-if="editing === d.id"><DeadlineForm :d="d" @saved="saved" @cancel="editing = null" /></template>
        <template v-else>
          <div class="dl-top">
            <div>
              <span class="kind">{{ KIND[d.kind] }}{{ d.weight != null ? ' · ' + d.weight + ' %' : '' }}</span>
              <h3>{{ d.course }} — {{ d.title }}</h3>
              <p class="muted">{{ fmtDue(d) }}{{ d.end_time ? '–' + d.end_time : '' }} · {{ leftLabel(d.days_left) }}</p>
            </div>
            <div class="dl-left num" :class="{ hot: d.days_left <= 3 }">{{ d.days_left }}<small>days</small></div>
          </div>
          <div class="dl-bar" :title="'done ' + fmtH(d.done_min) + ', planned ' + fmtH(d.planned_min) + ' of ' + d.hours_needed + ' h'">
            <i class="done" :style="{ width: pct(d) + '%' }"></i><i class="plan" :style="{ width: pctPlan(d) + '%' }"></i>
          </div>
          <p class="dl-fig"><b>{{ fmtH(d.done_min) }}</b> done · {{ fmtH(d.planned_min) }} planned · goal {{ d.hours_needed }} h{{ d.estimated ? ' (estimated)' : '' }}
            <span v-if="d.short_min >= 30" class="short"> · {{ fmtH(d.short_min) }} doesn't fit</span></p>
          <div class="actions" style="margin-top:8px">
            <button class="btn ghost small" @click="editing = d.id; adding = false">Edit</button>
            <button v-if="d.kind !== 'exam'" class="btn ghost small" @click="markDone(d)">Handed in</button>
            <button class="btn ghost small" @click="del(d)">Delete</button>
          </div>
        </template>
      </article>
    </div>

    <details class="howto" style="margin-top:28px" :open="!o.deadlines.length">
      <summary>Paste a list of deadlines</summary>
      <p class="muted">One per line: date and time | course | what | weight % | hours (optional). Lines that match an existing deadline update it.</p>
      <textarea rows="7" v-model="paste" placeholder="2026-12-11 18:30 | Intelligence artificielle | Examen final | 30&#10;2026-12-13 13:30 | Algo & structures de données | Examen final | 35 | 20h&#10;Dec 10 14:00 | Algo & structures de données | TP3 | 10"></textarea>
      <div class="actions"><button class="btn" :disabled="busy || !paste.trim()" @click="imp">Import</button></div>
    </details>
  </div>`,
};

/* ================================================================ root */
const VIEWS = { "/today": TodayView, "/week": WeekView, "/food": FoodView, "/body": BodyView, "/season": SeasonView, "/new-plan": NewPlanView,
  "/builder": BuilderView, "/settings": SettingsView, "/study": StudyView, "/setup": SetupView, "/myfoods": MyFoodsView, "/levels": SeasonView };
const NAV = [["/today", "Today"], ["/week", "Week"], ["/study", "Study"], ["/food", "Food"], ["/body", "Body"], ["/season", "Progress"], ["/settings", "Settings"]];

const App = {
  components: { Welcome },
  setup() {
    const ready = ref(false);
    onMounted(async () => {
      if (store.token) { try { store.user = await api("/api/me"); } catch { logout(); } }
      if (!store.token && route.path !== "/welcome") go("/welcome");
      if (store.token && route.path === "/welcome") go("/today");
      ready.value = true;
    });
    watch(() => route.path, (p) => { if (!store.token && p !== "/welcome") go("/welcome"); window.scrollTo(0, 0); });
    const view = computed(() => VIEWS[route.path] || TodayView);
    return { ready, route, store, view, NAV, theme, setTheme };
  },
  template: `
  <template v-if="ready">
    <Welcome v-if="route.path === '/welcome' || !store.token" />
    <div v-else class="shell">
      <header class="topbar">
        <a href="#/today" class="wordmark" aria-label="Taper home">
          <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden="true"><rect x="1" y="14" width="5" height="11" rx="1" fill="#a9bcc6"/><rect x="7" y="8" width="5" height="17" rx="1" fill="#e9a23b"/><rect x="13" y="3" width="5" height="22" rx="1" fill="#e2531b"/><rect x="19" y="12" width="5" height="13" rx="1" fill="#4fa387"/></svg>
          Taper</a>
        <nav class="nav"><a v-for="[p, l] in NAV" :key="p" :href="'#' + p" :class="{ on: route.path === p }" :aria-current="route.path === p ? 'page' : null">{{ l }}</a></nav>
        <span class="who" v-if="store.user">{{ store.user.name }}</span>
        <button class="theme-btn" @click="setTheme(theme.resolved === 'dark' ? 'light' : 'dark')" :aria-label="theme.resolved === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'" :title="theme.resolved === 'dark' ? 'Light theme' : 'Dark theme'">
          <svg v-if="theme.resolved === 'dark'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4.5"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
          <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M20.5 14.5A8.5 8.5 0 0 1 9.5 3.5a8.5 8.5 0 1 0 11 11z"/></svg>
        </button>
      </header>
      <main><Transition name="view" mode="out-in"><component :is="view" :key="route.path" /></Transition></main>
    </div>
    <Transition name="toast"><div v-if="store.toast" class="toast" role="status">{{ store.toast }}</div></Transition>
  </template>`,
};

const app = createApp(App);
app.component("FuelPlan", FuelPlan);
app.component("LevelCheckForm", LevelCheckForm);
app.mount("#app");
