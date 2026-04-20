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
        f"[bold white on blue]  POLYMARKET COPY TRADING BOT  [/]  "
        f"{mode_tag}  "
        f"[dim]Cycle #{cycle_count}  {now}[/dim]"
    )
    console.print()

    # ── Top Traders ──
    t_list = traders or models.get_tracked_traders()
    if t_list:
        tt = Table(box=box.ROUNDED, title="Top Sports Traders", title_style="bold cyan", expand=True)
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
    journal = models.get_trade_journal_summary()
    total = pnl.get("total_trades", 0)
    wins = pnl.get("win_count", 0)
    losses = pnl.get("loss_count", 0)
    realized = pnl.get("realized_pnl", 0)
    unrealized = pnl.get("unrealized_pnl", 0)
    wr = f"{wins/total*100:.0f}%" if total > 0 else "-"
    journal_closed = journal.get("closed_entries", 0) or 0
    journal_wins = journal.get("wins", 0) or 0
    journal_wr = f"{journal_wins/journal_closed*100:.0f}%" if journal_closed > 0 else "-"

    stats_lines = [
        f"  Bankroll     [cyan]${config.BANKROLL:,.0f}[/cyan]",
        f"  Stake        [cyan]{config.STAKE_PCT*100:.0f}%[/cyan] of whale",
        f"  Score Gate   [cyan]>={config.MIN_TRADER_SCORE:.0f}[/cyan]",
        f"  Confirm      [cyan]{config.MIN_SIGNAL_CONFIRM_SEC}s[/cyan] delay",
        f"  Trades       {total}  ([green]{wins}W[/green] / [red]{losses}L[/red])",
        f"  Win Rate     {wr}",
        f"  Journal      {journal.get('open_entries', 0)} open / {journal_closed} closed",
        f"  Journal PnL  {'[green]' if journal.get('realized_pnl', 0) >= 0 else '[red]'}${journal.get('realized_pnl', 0):,.2f}[/]",
        f"  Entry Drift  {journal.get('avg_entry_drift', 0):.3f}",
        f"  Journal WR   {journal_wr}",
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
