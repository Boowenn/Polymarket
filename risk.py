import time

import config
import liquidity
import models
import portfolio


class RiskCheck:
    """Hard risk gate for copy and strategy signals."""

    def _ordered_checks(self):
        return [
            self._check_duplicate,
            self._check_session_stop_loss,
            self._check_trader_quality,
            self._check_signal_age,
            self._check_whipsaw_trap,
            self._check_orderbook_liquidity,
            self._check_price_band,
            self._check_repeat_harvest,
            self._check_daily_risk_budget,
            self._check_market_exposure,
            self._check_trader_exposure,
            self._check_max_positions,
            self._check_cooldown,
        ]

    def check(self, trade_signal):
        for check_fn in self._ordered_checks():
            approved, reason = check_fn(trade_signal)
            if not approved:
                models.log_risk_event(
                    "BLOCKED",
                    (
                        f"{trade_signal.get('signal_source', 'copy')} "
                        f"{trade_signal.get('side', 'BUY')} "
                        f"{trade_signal.get('market_slug', 'unknown')}"
                    ),
                    reason,
                )
                return False, reason
        return True, "all checks passed"

    def check_repeat_entry_experiment(self, trade_signal):
        if not config.stage2_repeat_entry_experiment_enabled():
            return False, "stage2 experiment disabled"
        if trade_signal.get("signal_source", "copy") != "copy":
            return False, "stage2 experiment only applies to copy signals"

        approved, reason = self._check_repeat_harvest(trade_signal)
        if approved:
            return False, "repeat gate not triggered"

        experiment_entries = models.get_experiment_entry_count(
            config.REPEAT_ENTRY_EXPERIMENT_KEY,
            trade_signal.get("trader_wallet", ""),
            trade_signal.get("condition_id", ""),
            trade_signal.get("outcome", ""),
        )
        if experiment_entries >= config.REPEAT_ENTRY_EXPERIMENT_MAX_EXTRA_ENTRIES:
            return False, "stage2 repeat-entry experiment quota reached"

        for check_fn in self._ordered_checks():
            if check_fn == self._check_repeat_harvest:
                continue
            approved, reason = check_fn(trade_signal)
            if not approved:
                return False, reason

        return True, "repeat-entry experiment allowed"

    def _planned_value(self, signal):
        planned_value = signal.get("_planned_value")
        if planned_value is not None:
            return float(planned_value or 0)
        target_value = signal.get("target_value")
        if target_value is not None:
            desired_value = float(target_value or 0)
        else:
            desired_value = (
                float(signal.get("size", 0) or 0) * float(signal.get("price", 0) or 0) * config.STAKE_PCT
            )
        return min(desired_value, config.effective_max_trade_value())

    def _check_duplicate(self, signal):
        with models.db() as conn:
            row = conn.execute(
                "SELECT mirrored FROM trades WHERE id = ? AND mirrored = 1",
                (signal["id"],),
            ).fetchone()
        if row:
            return False, "already mirrored"
        return True, ""

    def _check_session_stop_loss(self, signal):
        if not config.session_stop_loss_enabled():
            return True, ""

        snapshot = portfolio.get_live_drawdown_snapshot()
        if not snapshot.get("stop_active"):
            return True, ""

        return False, (
            f"session stop active ({snapshot.get('total_pnl', 0):.2f} <= "
            f"-{snapshot.get('loss_limit_usdc', 0):.2f})"
        )

    def _check_trader_quality(self, signal):
        source = signal.get("signal_source", "copy")
        if source == "consensus":
            score = float(signal.get("signal_score", 0) or 0)
            if score < config.MIN_CONSENSUS_SCORE:
                return False, f"consensus too weak ({score:.1f} < {config.MIN_CONSENSUS_SCORE:.1f})"
            return True, ""

        profile = models.get_trader_profile(signal.get("trader_wallet", ""))
        if not profile:
            return False, "trader profile missing"
        if profile.get("status") != "approved":
            return False, f"trader not approved ({profile.get('status', 'unknown')})"
        if float(profile.get("quality_score", 0) or 0) < config.MIN_TRADER_SCORE:
            return False, f"trader score too low ({profile['quality_score']:.1f})"
        return True, ""

    def _check_signal_age(self, signal):
        age = time.time() - float(signal.get("timestamp", 0) or 0)
        if signal.get("signal_source", "copy") == "copy" and age < config.MIN_SIGNAL_CONFIRM_SEC:
            return False, f"waiting confirmation window ({age:.0f}s < {config.MIN_SIGNAL_CONFIRM_SEC}s)"
        if age > config.MAX_SIGNAL_AGE_SEC:
            return False, f"stale signal ({age:.0f}s old)"
        return True, ""

    def _check_orderbook_liquidity(self, signal):
        planned_size = float(signal.get("_planned_size", 0) or 0)
        if planned_size <= 0:
            return False, "planned size is 0"

        assessment = signal.get("_execution_assessment")
        if assessment is None:
            assessment = liquidity.assess_execution(signal, planned_size)
            signal["_execution_assessment"] = assessment
        if not assessment.get("ok"):
            return False, assessment.get("reason", "orderbook check failed")
        return True, ""

    def _check_whipsaw_trap(self, signal):
        if signal.get("signal_source", "copy") != "copy":
            return True, ""
        reversed_after = models.has_opposite_trade_after(
            signal.get("trader_wallet", ""),
            signal.get("condition_id", ""),
            signal.get("outcome", ""),
            signal.get("side", "BUY"),
            float(signal.get("timestamp", 0) or 0),
            within_sec=config.WHIPSAW_LOOKBACK_SEC,
        )
        if reversed_after:
            return False, "trader reversed same market after signal"
        return True, ""

    def _check_price_band(self, signal):
        price = float(signal.get("price", 0) or 0)
        if price <= 0:
            return False, "invalid price"
        if price < config.MIN_SIGNAL_PRICE or price > config.MAX_SIGNAL_PRICE:
            return False, (
                f"price outside copy band ({price:.3f} not in "
                f"{config.MIN_SIGNAL_PRICE:.2f}-{config.MAX_SIGNAL_PRICE:.2f})"
            )
        return True, ""

    def _check_daily_risk_budget(self, signal):
        if not config.capital_gates_enabled():
            return True, ""
        proposed = self._planned_value(signal)
        spent = models.get_daily_deployed_value()
        budget = config.effective_daily_risk_budget()
        if spent + proposed > budget:
            return False, (
                f"daily risk budget reached (${spent + proposed:.2f} > "
                f"${budget:.2f})"
            )
        return True, ""

    def _check_market_exposure(self, signal):
        if not config.capital_gates_enabled():
            return True, ""
        proposed = self._planned_value(signal)
        current = models.get_exposure_by_market(signal.get("condition_id", ""), signal.get("outcome", ""))
        cap = config.effective_bankroll() * config.MAX_MARKET_EXPOSURE_PCT
        if current + proposed > cap:
            return False, f"market exposure too high (${current + proposed:.2f} > ${cap:.2f})"
        return True, ""

    def _check_repeat_harvest(self, signal):
        if signal.get("signal_source", "copy") != "copy":
            return True, ""
        count = models.get_mirrored_entry_count(
            signal.get("trader_wallet", ""),
            signal.get("condition_id", ""),
            signal.get("outcome", ""),
        )
        if count >= config.MAX_TRADER_MARKET_ENTRIES_PER_DAY:
            return False, "already mirrored this trader/market today"
        return True, ""

    def _check_trader_exposure(self, signal):
        if signal.get("signal_source", "copy") != "copy":
            return True, ""
        if not config.capital_gates_enabled():
            return True, ""

        proposed = self._planned_value(signal)
        current = models.get_exposure_by_trader(signal.get("trader_wallet", ""))
        cap = config.effective_bankroll() * config.MAX_TRADER_EXPOSURE_PCT
        if current + proposed > cap:
            return False, f"trader exposure too high (${current + proposed:.2f} > ${cap:.2f})"
        return True, ""

    def _check_max_positions(self, signal):
        if not config.capital_gates_enabled():
            return True, ""
        count = models.get_open_position_count()
        if count >= config.MAX_POSITIONS:
            return False, f"max positions reached ({count}/{config.MAX_POSITIONS})"
        return True, ""

    def _check_cooldown(self, signal):
        source = signal.get("signal_source", "copy")
        trader_wallet = signal.get("trader_wallet") if source == "copy" else None
        recent = models.get_recent_mirrored_trade(
            signal.get("condition_id", ""),
            signal.get("outcome", ""),
            signal.get("side", "BUY"),
            config.TRADER_COOLDOWN_SEC,
            trader_wallet=trader_wallet,
        )
        if recent:
            return False, f"cooldown active ({config.TRADER_COOLDOWN_SEC}s)"
        return True, ""


risk_checker = RiskCheck()
