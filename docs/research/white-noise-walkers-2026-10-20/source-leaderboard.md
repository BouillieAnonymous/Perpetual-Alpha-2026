---
layout: default
title: Leaderboard
---

{% if jekyll.environment == "development" %}{% assign lb_url = site.data.leaderboard.dev_data_url | relative_url %}{% assign lb_practice_url = site.data.leaderboard.dev_practice_url | relative_url %}{% assign lb_chart_url = site.data.leaderboard.dev_chart_url | relative_url %}{% assign lb_practice_chart_url = site.data.leaderboard.dev_practice_chart_url | relative_url %}{% else %}{% assign lb_url = site.data.leaderboard.data_url %}{% assign lb_practice_url = site.data.leaderboard.practice_url %}{% assign lb_chart_url = site.data.leaderboard.chart_url %}{% assign lb_practice_chart_url = site.data.leaderboard.practice_chart_url %}{% endif %}
<div class="page-content lb">
    <div class="lb-head">
        <h1>Leaderboard</h1>
        <p class="lb-status" id="lb-status" aria-live="polite"><span class="lb-dot"></span><span id="lb-status-text">Loading…</span></p>
    </div>

    <nav class="lb-tabs" id="lb-tabs" aria-label="Standings" hidden>
        <button type="button" data-tab="live" id="lb-tab-live">Current phase</button>
        <button type="button" data-tab="practice">Practice (final)</button>
    </nav>

    <p class="lb-sort" id="lb-sort" hidden>Ranked by
        <button type="button" data-sort="score">Score</button>
        <button type="button" data-sort="return">PnL</button>
        <button type="button" data-sort="volatility">Volatility</button>
    </p>

    <div class="glass-card lb-card">
        <table class="lb-table" id="lb-table">
            <thead>
                <tr>
                    <th class="lb-rank">#</th>
                    <th class="lb-team">Team</th>
                    <th class="lb-num" data-col="return" title="Time-weighted net return up to the latest daily sample (12:00 UTC)">PnL</th>
                    <th class="lb-num" data-col="volatility" title="Volatility over the period: daily deviation × √n, the one in the score">Volatility</th>
                    <th class="lb-num" data-col="score" title="0.8 × Q + 0.2 × J (J = 0 until the jury votes)">Score</th>
                </tr>
            </thead>
            <tbody id="lb-body"></tbody>
        </table>
        <!-- Phones: one card per team, five columns of 8-9 decimals do not fit. -->
        <ol class="lb-cards" id="lb-cards"></ol>
        <p class="lb-empty" id="lb-empty" hidden></p>
    </div>

    <section class="lb-chart" id="lb-chart" hidden>
        <h2>Top 10 by PnL</h2>
        <div id="lb-chart-box"></div>
        <p class="lb-disclaimer">This chart shows, among the teams that have already traded, the 10 with the highest PnL at their latest equity snapshot, one point every 10 minutes; it is refreshed every 5 minutes, so the teams in it can change at every update. It is not the ranking: the standings and the score use only the daily sample at 12:00 UTC, so the order here can differ from the standings. Between two daily samples the lines are for information only.</p>
    </section>

    <section class="lb-formulas" id="lb-formulas" hidden>
        <h2>How the leaderboard is computed</h2>
        <ol id="lb-formulas-list"></ol>
    </section>
    <p class="lb-disclaimer">Standings are provisional until the organizers confirm the final results. The organizers may recompute the scores, re-apply the formula or change it where needed for a fair assessment of the competition, or to correct technical problems in the data or in the calculations.</p>
</div>

<script>
(function () {
    const URLS = { live: {{ lb_url | jsonify }}, practice: {{ lb_practice_url | jsonify }} };
    const CHART_URLS = { live: {{ lb_chart_url | jsonify }}, practice: {{ lb_practice_chart_url | jsonify }} };
    const REFRESH_MS = {{ site.data.leaderboard.refresh_seconds | default: 60 }} * 1000;
    // Practice returns are often a few millionths of the capital: 8 decimals, 9 for the score.
    const PCT_DIGITS = 8;
    const SCORE_DIGITS = 9;

    const boards = { live: null, practice: null };
    const charts = { live: null, practice: null };
    let tab = location.hash === "#practice" ? "practice" : "live";
    let sort = "score";
    let failed = false;

    const $ = (id) => document.getElementById(id);
    const el = (tag, cls, text) => {
        const n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text != null) n.textContent = text;
        return n;
    };

    // --- ranking: same rules as the organizer panel (pa_frontend lib/leaderboard.ts) ---

    // Volatility over the period (daily deviation × √n); an older file has the daily one only.
    const shownVol = (t) => (t.volatility_horizon !== undefined ? t.volatility_horizon : t.volatility);
    const byName = (a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase());

    // By score: eligible teams by rank, then the others (teams that traded first, then by name), all with
    // their figures. By PnL (highest first) or volatility (lowest first): a team not ranked shows its figures
    // only when final (while live it has never traded).
    function rankTeams(d, by) {
        const line = (t, rank, shown) => ({ t, rank, shown, inScore: t.eligible != null ? t.eligible : rank !== null });
        if (by === "score") {
            const ranked = (t) => typeof t.rank === "number";
            const top = d.teams.filter(ranked).sort((a, b) => a.rank - b.rank || byName(a, b));
            const rest = d.teams.filter((t) => !ranked(t)).sort((a, b) => (b.ranked - a.ranked) || byName(a, b));
            return [...top.map((t) => line(t, t.rank, true)), ...rest.map((t) => line(t, null, true))];
        }
        const value = (t) => (by === "return" ? t.return : shownVol(t));
        const dir = by === "return" ? -1 : 1;
        const top = d.teams.filter((t) => t.ranked && value(t) != null).sort((a, b) => dir * (value(a) - value(b)) || byName(a, b));
        const rest = d.teams.filter((t) => !top.includes(t)).sort((a, b) => (b.ranked - a.ranked) || byName(a, b));
        const shown = (t) => t.ranked || d.status === "final";
        return [...top.map((t, i) => line(t, i + 1, shown(t))), ...rest.map((t) => line(t, null, shown(t)))];
    }

    const pct = (v) => (v == null || !Number.isFinite(v) ? "–" : (v >= 0 ? "+" : "") + (v * 100).toFixed(PCT_DIGITS) + "%");
    const volPct = (v) => (v == null || !Number.isFinite(v) ? "–" : (v * 100).toFixed(PCT_DIGITS) + "%");
    const score = (v) => (v == null || !Number.isFinite(v) ? "–" : v.toFixed(SCORE_DIGITS));
    // 12 significant digits: ε / c is 0.1, not 0.09999999999999999.
    const num = (x) => String(Number(x.toPrecision(12)));
    const when = (iso) => new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC";

    function figures(l) {
        const ret = l.shown ? l.t.return : null;
        const vol = l.shown ? shownVol(l.t) : null;
        return {
            ret: pct(ret), vol: volPct(vol), score: score(l.t.score),
            color: ret == null || !Number.isFinite(ret) ? "" : ret >= 0 ? "up" : "down",
        };
    }

    // --- rendering ---

    function render() {
        const d = boards[tab];
        // The practice tab exists once its file is written.
        $("lb-tabs").hidden = !boards.practice;
        $("lb-tab-live").textContent = boards.live && boards.live.phase ? boards.live.phase : "Current phase";
        document.querySelectorAll("#lb-tabs button").forEach((b) => b.setAttribute("aria-current", b.dataset.tab === tab ? "page" : "false"));

        // A file without the score (older tracker) is ordered by PnL.
        const by = sort === "score" && d && !d.scoring ? "return" : sort;
        document.querySelectorAll("#lb-sort button").forEach((b) => {
            b.hidden = b.dataset.sort === "score" && !(d && d.scoring);
            b.setAttribute("aria-pressed", b.dataset.sort === by ? "true" : "false");
        });
        document.querySelectorAll("#lb-table th[data-col]").forEach((th) => th.classList.toggle("on", th.dataset.col === by));

        const body = $("lb-body");
        const cards = $("lb-cards");
        body.replaceChildren();
        cards.replaceChildren();
        const empty = $("lb-empty");
        const live = d && d.status !== "upcoming";
        $("lb-sort").hidden = !live;
        $("lb-table").hidden = !live;
        empty.hidden = !!live;
        if (!d) {
            empty.textContent = failed ? "The leaderboard is not available yet. Check back when the competition starts." : "Loading…";
            renderFormulas(null);
            renderChart();
            return;
        }
        if (!live) {
            empty.textContent = "The phase starts on " + when(d.from) + ": no figures before then.";
            renderFormulas(null);
            renderChart();
            return;
        }
        for (const l of rankTeams(d, by)) {
            const f = figures(l);
            const tr = el("tr", l.inScore ? "" : "lb-unranked");
            tr.appendChild(el("td", "lb-rank", l.rank == null ? "–" : String(l.rank)));
            tr.appendChild(el("td", "lb-team", l.t.name));
            tr.appendChild(el("td", "lb-num " + f.color + (by === "return" ? " on" : ""), f.ret));
            tr.appendChild(el("td", "lb-num" + (by === "volatility" ? " on" : ""), f.vol));
            tr.appendChild(el("td", "lb-num lb-score" + (by === "score" ? " on" : ""), f.score));
            body.appendChild(tr);

            const li = el("li", "lb-item" + (l.inScore ? "" : " lb-unranked"));
            const top = el("div", "lb-item-head");
            top.appendChild(el("span", "lb-rank", l.rank == null ? "–" : String(l.rank)));
            top.appendChild(el("span", "lb-team", l.t.name));
            li.appendChild(top);
            const dl = el("dl", "lb-item-figs");
            for (const [label, value, cls] of [["PnL", f.ret, f.color], ["Volatility", f.vol, ""], ["Score", f.score, "lb-score"]]) {
                const row = el("div");
                row.appendChild(el("dt", "", label));
                row.appendChild(el("dd", cls, value));
                dl.appendChild(row);
            }
            li.appendChild(dl);
            cards.appendChild(li);
        }
        renderFormulas(d);
        renderChart();
    }

    // The formulas behind the table, with the parameters of the file (fractional score only).
    function renderFormulas(d) {
        const box = $("lb-formulas");
        const s = d && d.scoring;
        box.hidden = !(s && s.method === "fractional");
        if (box.hidden) return;
        const cutoff = d.from.slice(11, 16);
        const days = d.min_days + " trading day" + (d.min_days === 1 ? "" : "s");
        const items = [
            ["PnL", " (return R): time-weighted net return up to the latest daily sample, R = (1 + r_1) × … × (1 + r_n) − 1, " +
                "where r_d = (E_d − transfers) / E_(d−1) − 1 and E_d is the Hyperliquid account value (open positions at the mark price, unrealized PnL included) " +
                "at the daily sample of day d: " + sampleRule(cutoff, s.sample_after_s) + ". " +
                "Money moved in or out is not a gain or a loss. PnL, volatility and score change once a day, with the new sample."],
            ["Volatility", " σ: volatility over the period, the same days as the PnL. σ = s × √n, where s is the " + s.std + " standard deviation (n − 1) " +
                "of the n daily returns of the completed days so far (from the second day) and n their number; after a missing day the return enters as r / √(days covered)."],
            ["Financial score", " Q = (max(R, 0) + ε) / (|R| + λ × σ + c), with R the PnL, σ the volatility, λ = " + s.lambda + ", c = " + s.c + ", ε = " + s.epsilon + ". " +
                "Each team is scored on its own figures only: the score does not change when other teams join or move. A loss scores below ε / c = " + num(s.epsilon / s.c) + "."],
            ["Score", " = " + s.w_financial + " × Q + " + s.w_jury + " × J, with J the jury score from 0 to 1: 0 until the jury votes, " +
                "so until then the score is at most " + s.w_financial + ". The position (#) follows the score."],
            ["In the score", ": at least " + days + " (a trade on the competition markets) in the phase, a start sample and at least 2 daily returns. " +
                "The first daily return comes with the second daily sample, so in the first two days of a phase nobody has a rank yet. " +
                "The other teams are listed below, dimmed, with their figures."],
        ];
        const list = $("lb-formulas-list");
        list.replaceChildren();
        for (const [head, text] of items) {
            const li = el("li");
            li.appendChild(el("strong", "", head));
            li.appendChild(document.createTextNode(text));
            list.appendChild(li);
        }
    }

    function status() {
        const s = $("lb-status");
        const txt = $("lb-status-text");
        s.className = "lb-status";
        const d = boards[tab];
        if (!d) {
            if (failed) txt.textContent = "Not available yet";
            return;
        }
        const phase = d.phase ? d.phase + " · " : "";
        if (d.status === "upcoming") {
            txt.textContent = phase + "Starts " + when(d.from);
        } else if (d.status === "final") {
            s.classList.add("final");
            txt.textContent = phase + "Final standings · " + when(d.from) + " to " + when(d.to);
        } else {
            s.classList.add("live");
            const mins = Math.max(0, Math.round((Date.now() - new Date(d.generated_at)) / 60000));
            txt.textContent = (phase || "Live · ") + "updated " + (mins < 1 ? "just now" : mins + " min ago");
        }
    }

    // How the daily sample is taken (same words as the panel, pa_frontend lib/leaderboard.ts sampleRule).
    function sampleRule(cutoff, after) {
        if (after > 0) {
            const m = Math.round(after / 60);
            return "the first equity snapshot of the round that starts at " + cutoff + " UTC (taken within " + m + " minute" + (m === 1 ? "" : "s") +
                " of it; for a team the round missed, the last snapshot before " + cutoff + ")";
        }
        return "the last equity snapshot before " + cutoff + " UTC";
    }

    // --- PnL chart: same look and rules as the panel (pa_frontend components/tradingChart.tsx) ---

    const LINES = ["#ff9f1a", "#22d3ee", "#4ade80", "#f472b6", "#f5f5f5", "#3b82f6", "#f87171", "#a78bfa", "#bef264", "#9ca3af"];
    const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
    const SVG = "http://www.w3.org/2000/svg";
    const two = (n) => String(n).padStart(2, "0");
    const dayLabel = (t) => { const d = new Date(t); return two(d.getUTCDate()) + " " + MONTHS[d.getUTCMonth()]; };
    const stamp = (t) => { const d = new Date(t); return dayLabel(t) + " " + two(d.getUTCHours()) + ":" + two(d.getUTCMinutes()) + " UTC"; };
    // 5 decimals: the chart file keeps 1e-7 of the capital, 0.00001%.
    const pct5 = (v) => (v >= 0 ? "+" : "") + (v * 100).toFixed(5) + "%";
    const chartState = { hidden: new Set(), focus: null, cursor: null, frame: null }; // focus: kept for the panel's look, unused here

    function svgEl(tag, attrs, text) {
        const n = document.createElementNS(SVG, tag);
        for (const k in attrs) n.setAttribute(k, attrs[k]);
        if (text != null) n.textContent = text;
        return n;
    }

    // A stable colour per team name: the hash picks a colour, a taken one moves to the next free.
    function colours(keys) {
        const out = new Map();
        const taken = new Set();
        for (const k of keys) {
            let h = 0;
            for (let i = 0; i < k.length; i++) h = (h * 31 + k.charCodeAt(i)) >>> 0;
            let c = h % LINES.length;
            for (let n = 0; n < LINES.length && taken.has(c); n++) c = (c + 1) % LINES.length;
            taken.add(c);
            out.set(k, LINES[c]);
        }
        return out;
    }

    // The lines of a chart file: one point per step (null: no snapshot), then the last snapshot.
    function chartSeries(f) {
        const t0 = Date.parse(f.start);
        const step = f.step_s * 1000;
        const seen = new Map();
        for (const t of f.teams) seen.set(t.name, (seen.get(t.name) || 0) + 1);
        return f.teams.slice().sort((a, b) => a.position - b.position).slice(0, 10).map((t) => {
            const points = [];
            t.values.forEach((v, i) => { if (v !== null) points.push({ t: t0 + i * step, v }); });
            const last = Date.parse(t.last_at);
            if (!points.length || last > points[points.length - 1].t) points.push({ t: last, v: t.pnl });
            return { key: seen.get(t.name) > 1 ? t.name + " #" + t.position : t.name, name: t.name, position: t.position, points };
        });
    }

    function valueAt(points, t) {
        let lo = 0, hi = points.length - 1, found = -1;
        while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            if (points[mid].t <= t) { found = mid; lo = mid + 1; } else hi = mid - 1;
        }
        return found < 0 ? null : points[found].v;
    }

    function renderChart() {
        const box = $("lb-chart-box");
        const section = $("lb-chart");
        const d = boards[tab];
        const f = charts[tab];
        // Only a chart of the same window and phase as the standings shown.
        const ok = d && f && d.status !== "upcoming" && f.teams && f.teams.length && Date.parse(f.start) === Date.parse(d.from) && f.phase === d.phase;
        section.hidden = !ok;
        if (!ok) return;
        const series = chartSeries(f);
        const colour = colours(series.map((s) => s.key));
        const compact = box.clientWidth < 600;
        const width = compact ? 520 : 960, height = compact ? 340 : 380, font = compact ? 13 : 10;
        // Value axis in its own gutter on the left: inside the plot its labels hid the
        // latest points, which are always on the right edge.
        const pad = { top: 16, right: compact ? 100 : 150, bottom: compact ? 34 : 30, left: compact ? 92 : 70 };
        const plotW = width - pad.left - pad.right, plotH = height - pad.top - pad.bottom;
        const shown = series.filter((s) => !chartState.hidden.has(s.key) && s.points.length);
        let t0 = Infinity, t1 = -Infinity, lo = 0, hi = 0;
        for (const s of series) for (const p of s.points) { if (p.t < t0) t0 = p.t; if (p.t > t1) t1 = p.t; }
        for (const s of (shown.length ? shown : series)) for (const p of s.points) { if (p.v < lo) lo = p.v; if (p.v > hi) hi = p.v; }
        if (!(t1 > t0)) { section.hidden = true; return; }
        const margin = (hi - lo || Math.abs(hi) || 1e-6) * 0.06;
        lo -= margin; hi += margin;
        const x = (t) => pad.left + ((t - t0) / (t1 - t0)) * plotW;
        const y = (v) => pad.top + plotH - ((v - lo) / (hi - lo)) * plotH;

        const fig = el("figure", "lb-term");
        const cap = el("figcaption", "lb-term-head");
        const left = el("span", "lb-term-title");
        left.appendChild(el("span", "lb-term-badge", "PNL"));
        left.appendChild(el("span", "lb-term-name", "Top " + series.length + " by PnL"));
        left.appendChild(el("span", "lb-term-sub", Math.round(f.step_s / 60) + "m · last snapshot"));
        cap.appendChild(left);
        cap.appendChild(el("span", "lb-term-sub", "upd " + stamp(Date.parse(f.generated_at))));
        fig.appendChild(cap);

        const svg = svgEl("svg", { viewBox: "0 0 " + width + " " + height, role: "img", "aria-label": "PnL of the top " + series.length + " teams", class: "lb-term-svg" });
        svg.appendChild(svgEl("rect", { x: 0, y: 0, width, height, fill: "#07090c" }));
        for (let i = 0; i < 5; i++) {
            const v = lo + ((hi - lo) * (i + 0.5)) / 5;
            svg.appendChild(svgEl("line", { x1: pad.left, x2: pad.left + plotW, y1: y(v), y2: y(v), stroke: "#141920" }));
            svg.appendChild(svgEl("text", { x: pad.left - 6, y: y(v) + font / 3, "text-anchor": "end", fill: "#6b7280", "font-size": font }, pct5(v)));
        }
        const DAY = 86400000;
        const first = Date.parse(d.from);
        const days = [];
        for (let t = first + Math.ceil((t0 - first) / DAY) * DAY; t <= t1; t += DAY) days.push(t);
        const every = Math.max(1, Math.ceil(days.length / (compact ? 4 : 10)));
        days.forEach((t, i) => {
            svg.appendChild(svgEl("line", { x1: x(t), x2: x(t), y1: pad.top, y2: pad.top + plotH, stroke: "#262b33", "stroke-dasharray": "2 3" }));
            if (i % every === 0) svg.appendChild(svgEl("text", { x: x(t), y: height - 10, "text-anchor": x(t) - pad.left < 30 ? "start" : "middle", fill: "#6b7280", "font-size": font }, dayLabel(t)));
        });
        svg.appendChild(svgEl("line", { x1: pad.left, x2: pad.left + plotW, y1: y(0), y2: y(0), stroke: "#4b5563" }));
        svg.appendChild(svgEl("line", { x1: pad.left + plotW, x2: pad.left + plotW, y1: pad.top, y2: pad.top + plotH, stroke: "#262b33" }));
        // Last line first: the teams higher up are drawn on top of the flat ones.
        for (const s of shown.slice().reverse()) {
            // The spacing of the series, without its last interval: the last point can
            // be the latest snapshot seconds after the last step, and taking it as the
            // spacing broke every other interval into a gap.
            let step = Infinity;
            const last = s.points.length > 2 ? s.points.length - 1 : s.points.length;
            for (let i = 1; i < last; i++) step = Math.min(step, s.points[i].t - s.points[i - 1].t);
            let cur = [];
            const flush = () => {
                if (!cur.length) return;
                const dim = chartState.focus !== null && chartState.focus !== s.key;
                svg.appendChild(svgEl("polyline", { points: cur.join(" "), fill: "none", stroke: colour.get(s.key), "stroke-opacity": dim ? 0.15 : 1,
                    "stroke-width": chartState.focus === s.key ? 2.2 : 1.3, "stroke-linejoin": "round" }));
                cur = [];
            };
            s.points.forEach((p, i) => {
                if (i > 0 && p.t - s.points[i - 1].t > 3 * step) flush();
                cur.push(x(p.t).toFixed(1) + "," + y(p.v).toFixed(1));
            });
            flush();
        }
        // Last-value tags on the right axis, pushed apart.
        const tagH = font + 4;
        const tags = shown.map((s) => ({ s, v: s.points[s.points.length - 1].v })).map((r) => Object.assign(r, { y0: y(r.v), y: y(r.v) })).sort((a, b) => a.y - b.y);
        // Pushed down from the top edge, then up from the bottom edge: every tag
        // stays inside the plot height (shifting them all up moved the highest out).
        const tagTop = pad.top + tagH / 2, tagBottom = pad.top + plotH - tagH / 2;
        for (let i = 0; i < tags.length; i++) tags[i].y = Math.max(tags[i].y, tagTop, i ? tags[i - 1].y + tagH : -Infinity);
        for (let i = tags.length - 1; i >= 0; i--) tags[i].y = Math.min(tags[i].y, tagBottom, i < tags.length - 1 ? tags[i + 1].y - tagH : Infinity);
        for (const tg of tags) {
            const g = svgEl("g", { opacity: chartState.focus !== null && chartState.focus !== tg.s.key ? 0.25 : 1 });
            g.appendChild(svgEl("line", { x1: pad.left + plotW, x2: pad.left + plotW + 4, y1: tg.y0, y2: tg.y, stroke: colour.get(tg.s.key) }));
            g.appendChild(svgEl("rect", { x: pad.left + plotW + 4, y: tg.y - tagH / 2, width: pad.right - 6, height: tagH - 1, fill: colour.get(tg.s.key) }));
            g.appendChild(svgEl("text", { x: pad.left + plotW + 8, y: tg.y + font / 3, fill: "#000", "font-size": font, "font-weight": 700 },
                compact ? pct5(tg.v) : pct5(tg.v) + " " + tg.s.name.slice(0, 9).toUpperCase()));
            svg.appendChild(g);
        }
        // Crosshair: every line at the time under the pointer (mouse, pen or touch).
        const cross = svgEl("g", { "pointer-events": "none" });
        svg.appendChild(cross);
        const drawCross = () => {
            cross.replaceChildren();
            const c = chartState.cursor;
            if (c === null) return;
            const rows = shown.map((s) => ({ s, v: valueAt(s.points, c) })).filter((r) => r.v !== null).sort((a, b) => b.v - a.v);
            const cx = x(c), rowH = font + 4, boxW = compact ? 330 : 250;
            const bx = cx + 12 + boxW > pad.left + plotW ? Math.max(pad.left, cx - 12 - boxW) : cx + 12;
            cross.appendChild(svgEl("line", { x1: cx, x2: cx, y1: pad.top, y2: pad.top + plotH, stroke: "#ff9f1a", "stroke-dasharray": "3 3" }));
            cross.appendChild(svgEl("rect", { x: bx, y: pad.top + 6, width: boxW, height: rowH + 8 + rows.length * rowH, fill: "#0f1319", stroke: "#262b33" }));
            cross.appendChild(svgEl("text", { x: bx + 8, y: pad.top + 6 + rowH, fill: "#ff9f1a", "font-size": font, "font-weight": 700 }, stamp(c)));
            rows.forEach((r, i) => {
                cross.appendChild(svgEl("rect", { x: bx + 8, y: pad.top + 10 + rowH * (i + 1), width: 8, height: 8, fill: colour.get(r.s.key) }));
                cross.appendChild(svgEl("text", { x: bx + 22, y: pad.top + 6 + rowH * (i + 2), fill: "#d4d4d8", "font-size": font }, r.s.name.slice(0, compact ? 18 : 22)));
                cross.appendChild(svgEl("text", { x: bx + boxW - 8, y: pad.top + 6 + rowH * (i + 2), "text-anchor": "end", fill: r.v >= 0 ? "#22c55e" : "#ef4444", "font-size": font }, pct5(r.v)));
            });
        };
        const onPointer = (e) => {
            const r = svg.getBoundingClientRect();
            const px = ((e.clientX - r.left) / r.width) * width;
            chartState.cursor = px < pad.left || px > pad.left + plotW ? null : t0 + ((px - pad.left) / plotW) * (t1 - t0);
            if (chartState.frame === null) chartState.frame = requestAnimationFrame(() => { chartState.frame = null; drawCross(); });
        };
        svg.addEventListener("pointermove", onPointer);
        svg.addEventListener("pointerdown", onPointer);
        svg.addEventListener("pointerleave", (e) => { if (e.pointerType === "touch") return; chartState.cursor = null; drawCross(); });
        fig.appendChild(svg);

        // Legend: a button per team hides or shows its line.
        const table = el("table", "lb-term-legend");
        const head = el("tr");
        for (const [txt, cls] of [["#", ""], ["Team", ""], ["PnL", "lb-num"]]) head.appendChild(el("th", cls, txt));
        const thead = el("thead"); thead.appendChild(head); table.appendChild(thead);
        const tbody = el("tbody");
        for (const s of series) {
            const last = s.points[s.points.length - 1];
            const off = chartState.hidden.has(s.key);
            const tr = el("tr", off ? "off" : "");
            tr.appendChild(el("td", "lb-term-pos", String(s.position)));
            const td = el("td");
            const b = el("button");
            b.type = "button";
            b.setAttribute("aria-pressed", off ? "false" : "true");
            b.title = off ? "Show this line" : "Hide this line";
            const sw = el("span", "lb-term-swatch");
            sw.style.background = off ? "#3a3f47" : colour.get(s.key);
            b.appendChild(sw);
            b.appendChild(document.createTextNode(s.name));
            b.addEventListener("click", () => { if (chartState.hidden.has(s.key)) chartState.hidden.delete(s.key); else chartState.hidden.add(s.key); renderChart(); });
            td.appendChild(b);
            tr.appendChild(td);
            tr.appendChild(el("td", "lb-num " + (last && last.v < 0 ? "down" : "up"), last ? pct5(last.v) : "–"));
            tbody.appendChild(tr);
        }
        table.appendChild(tbody);
        fig.appendChild(table);
        box.replaceChildren(fig);
        drawCross();
    }

    // --- data ---

    async function fetchBoard(url) {
        if (!url) throw new Error("data_url is not set in _data/leaderboard.yml");
        const res = await fetch(url + (url.includes("?") ? "&" : "?") + "t=" + Date.now(), { cache: "no-store" });
        if (!res.ok) throw new Error("HTTP " + res.status);
        const d = await res.json();
        if (!d || !Array.isArray(d.teams)) throw new Error("not a leaderboard");
        return d;
    }

    async function load() {
        // The practice file never changes once written: fetched until it exists (503 before the practice closes).
        const jobs = [fetchBoard(URLS.live).then((d) => { boards.live = d; failed = false; })];
        if (!boards.practice && URLS.practice) jobs.push(fetchBoard(URLS.practice).then((d) => { boards.practice = d; }));
        // The PnL chart of the phase, rewritten every 5 minutes; the practice one is frozen. A missing chart only hides the chart.
        if (CHART_URLS.live) jobs.push(fetchBoard(CHART_URLS.live).then((c) => { charts.live = c; }));
        if (!charts.practice && CHART_URLS.practice) jobs.push(fetchBoard(CHART_URLS.practice).then((c) => { charts.practice = c; }));
        const out = await Promise.allSettled(jobs);
        if (out[0].status === "rejected") {
            console.error("leaderboard:", out[0].reason);
            if (!boards.live) failed = true;
        }
        if (tab === "practice" && !boards.practice) tab = "live";
        render();
        status();
    }

    document.querySelectorAll("#lb-sort button").forEach((b) => b.addEventListener("click", () => { sort = b.dataset.sort; render(); }));
    document.querySelectorAll("#lb-tabs button").forEach((b) => b.addEventListener("click", () => {
        tab = b.dataset.tab;
        history.replaceState(null, "", tab === "practice" ? "#practice" : location.pathname + location.search);
        render();
        status();
    }));

    render();
    load();
    setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
    setInterval(status, 30000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
    // The chart picks its drawing (wide or narrow) from its width.
    let resizeTimer = null;
    window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(renderChart, 150); });
})();
</script>
