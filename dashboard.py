from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
from rich import box

import config
import models


console = Console()


def _ts(ts):
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def render_dashboard(traders=None, cycle_count=0):
    """Render the full dashboard to terminal."""
    console.clear()

    # ── Header ──
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode_tag = "[yellow] WATCH [/yellow]" if config.DRY_RUN else "[red bold] LIVE [/red bold]"
    console.print(
        f"[bold white on blue]  POLYMARKET {config.market_scope_label().upper()} COPY TRADING BOT  [/]  "
        f"{mode_tag}  "
        f"[dim]Cycle #{cycle_count}  {now}[/dim]"
    )
    console.print()

    # ── Top Traders ──
    t_list = traders or models.get_tracked_traders()
    if t_list:
        tt = Table(
            box=box.ROUNDED,
            title=f"Top {config.market_scope_label()} Traders",
            title_style="bold cyan",
            expand=True,
        )
        tt.add_column("#", width=3, style="dim")
        tt.add_column("Trader", style="bold", max_width=22)
        tt.add_column("Status", width=10)
        tt.add_column("Score", justify="right", width=6)
        tt.add_column("PnL", justify="right", width=14)
        tt.add_column("Volume", justify="right", width=12)
        for t in t_list:
            pnl_s = f"${t['pnl']:,.2f}"
            status = t.get("status", "observe")
            if status == "approved":
                status_text = Text("approved", style="green")
            elif status == "blocked":
                status_text = Text("blocked", style="red")
            else:
                status_text = Text("observe", style="yellow")
            tt.add_row(
                str(t["rank"]),
                t["username"][:20],
                status_text,
                f"{float(t.get('quality_score', 0) or 0):.0f}",
                Text(pnl_s, style="green" if t["pnl"] >= 0 else "red"),
                f"${t['volume']:,.0f}",
            )
        console.print(tt)
        console.print()

    # ── Recent Signals ──
    recent = models.get_recent_trades(12)
    st = Table(box=box.ROUNDED, title="Recent Trade Signals", title_style="bold yellow", expand=True)
    st.add_column("Time", width=8)
    st.add_column("Trader", width=10)
    st.add_column("Src", width=9)
    st.add_column("Side", width=4)
    st.add_column("Market", min_width=20)
    st.add_column("Qty", justify="right", width=7)
    st.add_column("Price", justify="right", width=7)
    st.add_column("Status", width=9)

    for t in recent:
        side_style = "green bold" if t["side"] == "BUY" else "red bold"
        if t["mirrored"]:
            status = f"[green]{t.get('our_status', 'ok')}[/green]"
        else:
            status = "[dim]signal[/dim]"
        slug = t.get("market_slug") or t.get("condition_id", "")[:18]
        st.add_row(
            _ts(t["timestamp"]),
            (t.get("trader_username") or t["trader_wallet"])[:8],
            (t.get("signal_source") or "copy")[:8],
            Text(t["side"], style=side_style),
            slug[:28],
            f"{t['size']:.1f}",
            f"${t['price']:.2f}",
            status,
        )
    console.print(st)
    console.print()

    # ── Stats + Risk side by side ──
    pnl = models.get_latest_pnl()
    performance = models.get_performance_snapshot()
    realized = performance.get("realized_pnl", 0)
    unrealized = pnl.get("unrealized_pnl", 0)
    resolved = performance.get("closed_entries", 0) or 0
    open_entries = performance.get("open_entries", 0) or 0
    simulated = performance.get("simulated_entries", 0) or 0
    wins = performance.get("wins", 0) or 0
    losses = performance.get("losses", 0) or 0
    flats = performance.get("flat_count", 0) or 0
    decided = performance.get("decision_count", 0) or 0
    shadow_entries = performance.get("shadow_entries", 0) or 0
    shadow_open = performance.get("shadow_open_entries", 0) or 0
    shadow_closed = performance.get("shadow_closed_entries", 0) or 0
    stage2_entries = performance.get("repeat_entry_experiment_entries", 0) or 0
    stage2_open = performance.get("repeat_entry_experiment_open_entries", 0) or 0
    stage2_closed = performance.get("repeat_entry_experiment_closed_entries", 0) or 0
    wr = (
        f"{performance.get('win_rate', 0):.0f}%"
        if performance.get("win_rate") is not None
        else "N/A"
    )

    stats_lines = [
        f"  Bankroll     [cyan]${config.effective_bankroll():,.0f}[/cyan]",
        f"  Stake        [cyan]{config.STAKE_PCT*100:.0f}%[/cyan] of whale",
        f"  Universe     [cyan]{config.market_scope_label()}[/cyan]",
        f"  Discovery    {config.LEADERBOARD_CATEGORY} {config.discovery_label()}",
        f"  Monitor      {config.monitored_trader_limit()} discovered traders",
        f"  Workers      {config.MONITOR_FETCH_WORKERS} parallel fetchers",
        f"  Score Gate   [cyan]>={config.MIN_TRADER_SCORE:.0f}[/cyan]",
        f"  Confirm      [cyan]{config.MIN_SIGNAL_CONFIRM_SEC}s[/cyan] delay",
        f"  Simulated    {simulated} entries",
        f"  Shadow       {shadow_entries} blocked samples ({shadow_open} open / {shadow_closed} closed)",
        (
            f"  Stage2       {stage2_entries} repeat-entry experiment "
            f"({stage2_open} open / {stage2_closed} closed)"
        ),
        f"  Closed       {resolved}  ([green]{wins}W[/green] / [red]{losses}L[/red] / {flats} flat)",
        f"  Win Rate     {wr}  on {decided} decided trades",
        f"  Open         {open_entries}",
        (
            f"  Research     budget ${config.effective_daily_risk_budget():,.0f}, "
            f"capital gates {'off' if config.PAPER_IGNORE_CAPITAL_GATES else 'on'}"
            if config.DRY_RUN
            else "  Research     live execution"
        ),
        f"  Journal PnL  {'[green]' if realized >= 0 else '[red]'}${realized:,.2f}[/]",
        f"  Entry Drift  {performance.get('avg_entry_drift', 0):.3f}",
        f"  Basis        win rate uses executed fills; shadow samples tracked separately",
        f"  Realized     {'[green]' if realized >= 0 else '[red]'}${realized:,.2f}[/]",
        f"  Unrealized   {'[green]' if unrealized >= 0 else '[red]'}${unrealized:,.2f}[/]",
    ]
    stats_panel = Panel("\n".join(stats_lines), title="Stats", border_style="magenta", expand=True)

    risk_logs = models.get_recent_risk_logs(5)
    if risk_logs:
        risk_lines = []
        for r in risk_logs:
            risk_lines.append(
                f"  [dim]{_ts(r['timestamp'])}[/dim]  {r['event'][:12]:12s}  {(r['details'] or '')[:30]}"
            )
        risk_text = "\n".join(risk_lines)
    else:
        risk_text = "  [dim]No risk events[/dim]"
    risk_panel = Panel(risk_text, title="Risk Log", border_style="red", expand=True)

    console.print(Columns([stats_panel, risk_panel], equal=True, expand=True))
    console.print()
    console.print("[dim]  Ctrl+C to stop  |  Logs: copybot.log  |  Config: .env[/dim]")
