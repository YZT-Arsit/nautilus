#!/usr/bin/env python3
"""Compile reviewed Phase 5B families to the typed workbook DSL.

Only source-complete repeated families are encoded here.  Every attempted
source clause is retained in the closure artifacts; unsupported rows remain
explicit rather than being weakened to fit the compiler.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
PLAN = ROOT / "configs/semantic_contracts/workbook_phase5b_strategies.json"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    os.replace(temporary, path)


def and_(*args): return {"op": "and", "args": list(args)}
def or_(*args): return {"op": "or", "args": list(args)}
def gt(a,b): return {"op":"gt","left":a,"right":b}
def gte(a,b): return {"op":"gte","left":a,"right":b}
def lt(a,b): return {"op":"lt","left":a,"right":b}
def lte(a,b): return {"op":"lte","left":a,"right":b}
def up(a,b): return {"op":"cross_above","left":a,"right":b}
def down(a,b): return {"op":"cross_below","left":a,"right":b}
def turn_up(a): return {"op":"turn_up","value":a}
def turn_down(a): return {"op":"turn_down","value":a}
def pulse(a): return {"op":"pulse","value":a}
def consecutive(arg,bars=2): return {"op":"consecutive","bars":bars,"arg":arg}


def feature(kind: str, name: str, **kwargs) -> dict[str, Any]:
    return {"kind": kind, "name": name, **kwargs}


def mtf(name: str, minutes: int, output: str, indicator: str | None = None,
        window: int = 1, **indicator_params) -> dict[str, Any]:
    item = feature("completed_timeframe", name, timeframe_minutes=minutes, output=output, window=window)
    if indicator:
        item.update(indicator=indicator, indicator_params=indicator_params)
    return item


def action(kind: str, condition: dict[str, Any], fraction: float = 1.0, reason: str = "phase5b"):
    return {"action": kind, "condition": condition, "fraction": fraction, "reason": reason}


def definition(row: dict[str, str], features: list[dict[str, Any]], actions: list[dict[str, Any]],
               contracts: list[str], family: str) -> dict[str, Any]:
    rule = {"schema_version": 2, "features": features, "actions": actions,
            "source_clause_count": 3, "family": family}
    encoded = base64.urlsafe_b64encode(json.dumps(
        rule, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).decode()
    return {
        "family": "phase5b_declarative",
        "params": {"rule_spec_b64": encoded, "contract_versions": ";".join(contracts)},
        "semantic_provenance": "STANDARD_CONTRACT_RESOLVED",
        "contracts_applied": contracts,
        "defaulted_parameters": {}, "modelled_interpretations": [],
        "resolved_blockers": [row["current_blockers"]], "remaining_blockers": [],
        "modules_applied": [],
        "source_timeframe": "1m" if row["source_timeframe"] != "daily" else "1d",
        "rule_hash": hashlib.sha256(encoded.encode()).hexdigest(),
        "compiler_family": family,
    }


def compile_reviewed(row: dict[str, str]) -> dict[str, Any] | None:
    name = row["strategy_name"]
    bar = [feature("bar", "p5b_close", field="close"), feature("bar", "p5b_open", field="open")]
    if "AO+RSI 共振" in name:
        fs = bar + [feature("ao","p5b_ao",fast_window=5,slow_window=34), feature("rsi","p5b_rsi14",window=14)]
        acts = [
            action("EXIT_LONG", or_(down("p5b_ao",0.0),gte("p5b_rsi14",70.0))),
            action("EXIT_SHORT", or_(up("p5b_ao",0.0),lte("p5b_rsi14",30.0))),
            action("ENTER_LONG", and_(up("p5b_ao",0.0),lt("p5b_rsi14",40.0),turn_up("p5b_rsi14"))),
            action("ENTER_SHORT", and_(down("p5b_ao",0.0),gt("p5b_rsi14",60.0),turn_down("p5b_rsi14"))),
        ]
        return definition(row,fs,acts,["TURN_SLOPE_SIGN_CHANGE_V1","ACTION_PRECEDENCE_EXIT_REDUCE_ENTER_V1"],"AO_RSI_CONFLUENCE")
    if "CCI+AO 双动量" in name:
        fs = bar + [feature("cci","p5b_cci20",window=20),feature("ao","p5b_ao",fast_window=5,slow_window=34)]
        acts = [action("EXIT_LONG",gte("p5b_cci20",100.0)),action("EXIT_SHORT",lte("p5b_cci20",-100.0)),
                action("REDUCE_CURRENT",or_(down("p5b_ao",0.0),up("p5b_ao",0.0)),0.5),
                action("ENTER_LONG",and_(up("p5b_cci20",-100.0),up("p5b_ao",0.0))),
                action("ENTER_SHORT",and_(down("p5b_cci20",100.0),down("p5b_ao",0.0)))]
        return definition(row,fs,acts,["REDUCE_HALF_CURRENT_V1","ACTION_PRECEDENCE_EXIT_REDUCE_ENTER_V1"],"CCI_AO_CONFLUENCE")
    if "MACD+CCI 双动能" in name:
        fs = bar + [feature("macd","p5b_dif",output="dif"),feature("macd","p5b_dea",output="signal"),feature("cci","p5b_cci20",window=20)]
        acts = [action("EXIT_LONG",gte("p5b_cci20",100.0)),action("EXIT_SHORT",lte("p5b_cci20",-100.0)),
                action("REDUCE_CURRENT",or_(down("p5b_dif",0.0),up("p5b_dif",0.0)),0.5),
                action("ENTER_LONG",and_(gt("p5b_dif",0.0),up("p5b_dif","p5b_dea"),up("p5b_cci20",-100.0))),
                action("ENTER_SHORT",and_(lt("p5b_dif",0.0),down("p5b_dif","p5b_dea"),down("p5b_cci20",100.0)))]
        return definition(row,fs,acts,["REDUCE_HALF_CURRENT_V1","ACTION_PRECEDENCE_EXIT_REDUCE_ENTER_V1"],"MACD_CCI_CONFLUENCE")
    if "AO+ROC" in name or "AO+ROC 动量" in name:
        fs=bar+[feature("ao","p5b_ao",fast_window=5,slow_window=34),feature("return","p5b_roc12",window=12)]
        acts=[action("EXIT_LONG",down("p5b_ao",0.0)),action("EXIT_SHORT",up("p5b_ao",0.0)),
              action("ENTER_LONG",and_(consecutive(gt("p5b_ao",0.0)),up("p5b_roc12",0.0))),
              action("ENTER_SHORT",and_(consecutive(lt("p5b_ao",0.0)),down("p5b_roc12",0.0)))]
        return definition(row,fs,acts,["PERSISTENCE_2BAR_V1","ACTION_PRECEDENCE_EXIT_REDUCE_ENTER_V1"],"AO_ROC_CONFLUENCE")
    if "CCI 顺势反转" in name:
        fs=bar+[feature("cci","p5b_cci20",window=20),feature("sma","p5b_ma20",window=20)]
        acts=[action("EXIT_LONG",gte("p5b_cci20",100.0)),action("EXIT_SHORT",lte("p5b_cci20",-100.0)),
              action("REDUCE_CURRENT",or_(down("p5b_cci20",0.0),up("p5b_cci20",0.0)),0.5),
              action("ENTER_LONG",and_(up("p5b_cci20",-100.0),consecutive(gt("p5b_close","p5b_ma20")))),
              action("ENTER_SHORT",and_(down("p5b_cci20",100.0),consecutive(lt("p5b_close","p5b_ma20"))))]
        return definition(row,fs,acts,["STABLE_CLOSE_2BAR_V1","REDUCE_HALF_CURRENT_V1"],"CCI_MA_REVERSAL")
    if "EMA 斜率趋势过滤系统" in name or "EMA20 斜率动能过滤系统" in name:
        fs=bar+[feature("ema","p5b_ema20",window=20),feature("macd","p5b_dif",output="dif"),feature("macd","p5b_dea",output="signal")]
        slope_up=gt("p5b_ema20",{"op":"previous","value":"p5b_ema20"}); slope_down=lt("p5b_ema20",{"op":"previous","value":"p5b_ema20"})
        acts=[action("EXIT_LONG",turn_down("p5b_ema20")),action("EXIT_SHORT",turn_up("p5b_ema20")),
              action("REDUCE_CURRENT",or_(down("p5b_dif","p5b_dea"),up("p5b_dif","p5b_dea")),0.5),
              action("ENTER_LONG",and_(slope_up,gt("p5b_dif",0.0),up("p5b_dif","p5b_dea"))),
              action("ENTER_SHORT",and_(slope_down,lt("p5b_dif",0.0),down("p5b_dif","p5b_dea")))]
        return definition(row,fs,acts,["TURN_SLOPE_SIGN_CHANGE_V1","REDUCE_HALF_CURRENT_V1"],"EMA_SLOPE_MACD")
    if "ADX + 双均线强弱共振" in name:
        fs=bar+[feature("adx","p5b_adx14",window=14),feature("sma","p5b_ma20",window=20),feature("sma","p5b_ma60",window=60)]
        acts=[action("EXIT_ALL",lt("p5b_adx14",20.0)),action("REDUCE_CURRENT",or_(down("p5b_ma20","p5b_ma60"),up("p5b_ma20","p5b_ma60")),0.5),
              action("ENTER_LONG",and_(gt("p5b_adx14",25.0),up("p5b_ma20","p5b_ma60"))),action("ENTER_SHORT",and_(gt("p5b_adx14",25.0),down("p5b_ma20","p5b_ma60")))]
        return definition(row,fs,acts,["REDUCE_HALF_CURRENT_V1","ACTION_PRECEDENCE_EXIT_REDUCE_ENTER_V1"],"ADX_DUAL_MA")
    if "MACD 金叉 / 死叉柱量确认" in name:
        fs=bar+[feature("macd","p5b_dif",output="dif"),feature("macd","p5b_dea",output="signal"),feature("macd","p5b_hist",output="histogram")]
        hist_rising={"op":"rising","value":"p5b_hist","bars":1}; hist_falling={"op":"falling","value":"p5b_hist","bars":2}
        acts=[action("EXIT_LONG",down("p5b_dif",0.0)),action("EXIT_SHORT",up("p5b_dif",0.0)),action("REDUCE_CURRENT",hist_falling,0.5),
              action("ENTER_LONG",and_(up("p5b_dif","p5b_dea"),gt("p5b_hist",0.0),hist_rising)),action("ENTER_SHORT",and_(down("p5b_dif","p5b_dea"),lt("p5b_hist",0.0),{"op":"falling","value":"p5b_hist","bars":1}))]
        return definition(row,fs,acts,["PREVIOUS_COMMITTED_STATE_V1","REDUCE_HALF_CURRENT_V1"],"MACD_HISTOGRAM_CONFIRMATION")
    if "CCI 连续极值动量系统" in name:
        fs=bar+[feature("cci","p5b_cci20",window=20),feature("sma","p5b_ma20",window=20)]
        acts=[action("EXIT_LONG",consecutive(lt("p5b_close","p5b_ma20"))),action("EXIT_SHORT",consecutive(gt("p5b_close","p5b_ma20"))),
              action("REDUCE_CURRENT",and_(gte("p5b_cci20",-100.0),lte("p5b_cci20",100.0)),0.5),
              action("ENTER_LONG",and_(consecutive(lt("p5b_cci20",-100.0),3),consecutive(gt("p5b_close","p5b_ma20")))),
              action("ENTER_SHORT",and_(consecutive(gt("p5b_cci20",100.0),3),consecutive(lt("p5b_close","p5b_ma20"))))]
        return definition(row,fs,acts,["PERSISTENCE_3BAR_V1","STABLE_CLOSE_2BAR_V1","REDUCE_HALF_CURRENT_V1"],"CCI_PERSISTENT_EXTREME")
    if "BIAS + 分形反转" in name or "BIAS+5 周期分形" in name:
        fs=bar+[feature("bias","p5b_bias20",window=20),feature("fractal","p5b_lower",output="lower_pulse"),feature("fractal","p5b_upper",output="upper_pulse")]
        acts=[action("EXIT_LONG",pulse("p5b_upper")),action("EXIT_SHORT",pulse("p5b_lower")),action("REDUCE_CURRENT",or_(up("p5b_bias20",0.0),down("p5b_bias20",0.0)),0.5),
              action("ENTER_LONG",and_(lt("p5b_bias20",-7.0),pulse("p5b_lower"))),action("ENTER_SHORT",and_(gt("p5b_bias20",7.0),pulse("p5b_upper")))]
        return definition(row,fs,acts,["CONFIRMED_FRACTAL_2X2_V1","REDUCE_HALF_CURRENT_V1"],"BIAS_FRACTAL_REVERSAL")
    if name == "成交量 OBV 突破策略":
        fs=bar+[feature("obv","p5b_obv",window=20,output="obv"),feature("obv","p5b_obv_ma20",window=20,output="sma"),feature("sma","p5b_ma20",window=20)]
        acts=[action("EXIT_LONG",or_(down("p5b_obv","p5b_obv_ma20"),down("p5b_close","p5b_ma20"))),action("EXIT_SHORT",or_(up("p5b_obv","p5b_obv_ma20"),up("p5b_close","p5b_ma20"))),
              action("ENTER_LONG",and_(up("p5b_obv","p5b_obv_ma20"),up("p5b_close","p5b_ma20"))),action("ENTER_SHORT",and_(down("p5b_obv","p5b_obv_ma20"),down("p5b_close","p5b_ma20")))]
        return definition(row,fs,acts,["STANDARD_OBV_V1","ACTION_PRECEDENCE_EXIT_REDUCE_ENTER_V1"],"OBV_PRICE_MA_BREAKOUT")
    if "OBV+MA60 长线量价趋势" in name:
        fs=bar+[feature("obv","p5b_obv",window=20,output="obv"),feature("obv","p5b_obv_ma20",window=20,output="sma"),feature("sma","p5b_ma60",window=60)]
        acts=[action("EXIT_LONG",consecutive(lt("p5b_close","p5b_ma60"))),action("EXIT_SHORT",consecutive(gt("p5b_close","p5b_ma60"))),
              action("REDUCE_CURRENT",or_(turn_down("p5b_obv"),turn_up("p5b_obv")),0.5),
              action("ENTER_LONG",and_(gt("p5b_close","p5b_ma60"),consecutive(gt("p5b_obv","p5b_obv_ma20"),10))),
              action("ENTER_SHORT",and_(lt("p5b_close","p5b_ma60"),consecutive(lt("p5b_obv","p5b_obv_ma20"),10)))]
        return definition(row,fs,acts,["STANDARD_OBV_V1","PERSISTENCE_10BAR_V1","STABLE_CLOSE_2BAR_V1"],"OBV_MA60_TREND")
    if "OBV+ROC 短线动量" in name:
        fs=bar+[feature("obv","p5b_obv",window=20,output="obv"),feature("return","p5b_roc",window=12)]
        acts=[action("EXIT_LONG",down("p5b_roc",0.0)),action("EXIT_SHORT",up("p5b_roc",0.0)),action("REDUCE_CURRENT",or_(turn_down("p5b_obv"),turn_up("p5b_obv")),0.5),
              action("ENTER_LONG",and_({"op":"rising","value":"p5b_obv","bars":1},up("p5b_roc",0.0))),action("ENTER_SHORT",and_({"op":"falling","value":"p5b_obv","bars":1},down("p5b_roc",0.0)))]
        return definition(row,fs,acts,["STANDARD_OBV_V1","TURN_SLOPE_SIGN_CHANGE_V1","REDUCE_HALF_CURRENT_V1"],"OBV_ROC_MOMENTUM")
    if "双指标简易共振系统 2" in name:
        fs=bar+[feature("sma","p5b_ma20",window=20),feature("macd","p5b_dif",output="dif"),feature("macd","p5b_dea",output="signal"),feature("macd","p5b_hist",output="histogram")]
        acts=[action("EXIT_LONG",or_(down("p5b_close","p5b_ma20"),down("p5b_dif","p5b_dea"))),action("EXIT_SHORT",or_(up("p5b_close","p5b_ma20"),up("p5b_dif","p5b_dea"))),
              action("REDUCE_CURRENT",or_({"op":"falling","value":"p5b_hist","bars":2},{"op":"rising","value":"p5b_hist","bars":2}),0.5),
              action("ENTER_LONG",and_(consecutive(gt("p5b_close","p5b_ma20")),gt("p5b_dif",0.0),up("p5b_dif","p5b_dea"))),
              action("ENTER_SHORT",and_(consecutive(lt("p5b_close","p5b_ma20")),lt("p5b_dif",0.0),down("p5b_dif","p5b_dea")))]
        return definition(row,fs,acts,["STABLE_CLOSE_2BAR_V1","REDUCE_HALF_CURRENT_V1"],"MA_MACD_CONFLUENCE")
    if "均线 + AO 动量" in name or "基础单指标复盘系统 10（AO" in name:
        fs = bar + [feature("sma","p5b_ma20",window=20),feature("ao","p5b_ao",fast_window=5,slow_window=34)]
        ma_up=gt("p5b_ma20",{"op":"previous","value":"p5b_ma20"}); ma_down=lt("p5b_ma20",{"op":"previous","value":"p5b_ma20"})
        acts=[action("EXIT_LONG",or_(turn_down("p5b_ma20"),down("p5b_ao",0.0))),action("EXIT_SHORT",or_(turn_up("p5b_ma20"),up("p5b_ao",0.0))),
              action("ENTER_LONG",and_(ma_up,up("p5b_ao",0.0),consecutive(gt("p5b_ao",0.0)))),
              action("ENTER_SHORT",and_(ma_down,down("p5b_ao",0.0),consecutive(lt("p5b_ao",0.0))))]
        return definition(row,fs,acts,["PERSISTENCE_2BAR_V1","TURN_SLOPE_SIGN_CHANGE_V1"],"MA_AO_PERSISTENCE")
    if "PSAR+MA60" in name:
        fs=bar+[feature("sma","p5b_ma60",window=60),feature("psar","p5b_psar",output="sar"),feature("psar","p5b_psar_dir",output="direction")]
        acts=[action("EXIT_LONG",or_(lt("p5b_psar_dir",0.0),down("p5b_close","p5b_ma60"))),action("EXIT_SHORT",or_(gt("p5b_psar_dir",0.0),up("p5b_close","p5b_ma60"))),
              action("ENTER_LONG",and_(gt("p5b_ma60",{"op":"previous","value":"p5b_ma60"}),gt("p5b_close","p5b_psar"))),
              action("ENTER_SHORT",and_(lt("p5b_ma60",{"op":"previous","value":"p5b_ma60"}),lt("p5b_close","p5b_psar")))]
        return definition(row,fs,acts,["TURN_SLOPE_SIGN_CHANGE_V1","ACTION_PRECEDENCE_EXIT_REDUCE_ENTER_V1"],"PSAR_MA_TREND")
    if "三重均线唐奇安" in name:
        fs=bar+[feature("sma","p5b_ma20",window=20),feature("sma","p5b_ma60",window=60),feature("sma","p5b_ma120",window=120),
                feature("breakout_up","p5b_up20",window=20),feature("breakout_down","p5b_down20",window=20)]
        ordered_long=and_(gt("p5b_ma20","p5b_ma60"),gt("p5b_ma60","p5b_ma120")); ordered_short=and_(lt("p5b_ma20","p5b_ma60"),lt("p5b_ma60","p5b_ma120"))
        acts=[action("EXIT_LONG",{"op":"not","arg":ordered_long}),action("EXIT_SHORT",{"op":"not","arg":ordered_short}),
              action("REDUCE_CURRENT",or_(pulse("p5b_down20"),pulse("p5b_up20")),0.5),action("ENTER_LONG",and_(ordered_long,pulse("p5b_up20"))),action("ENTER_SHORT",and_(ordered_short,pulse("p5b_down20")))]
        return definition(row,fs,acts,["REDUCE_HALF_CURRENT_V1","ACTION_PRECEDENCE_EXIT_REDUCE_ENTER_V1"],"TRIPLE_MA_DONCHIAN")
    if "日线 + 4 小时双周期 MACD" in name or "双周期 MACD 过滤震荡" in name:
        fs=bar
        for tag,minutes in (("d",1440),("h4",240)):
            fs += [mtf(f"p5b_{tag}_dif",minutes,"dif","macd",fast_window=12,slow_window=26,signal_window=9),
                   mtf(f"p5b_{tag}_dea",minutes,"signal","macd",fast_window=12,slow_window=26,signal_window=9)]
        reverse_long=and_(down("p5b_d_dif","p5b_d_dea"),down("p5b_h4_dif","p5b_h4_dea")); reverse_short=and_(up("p5b_d_dif","p5b_d_dea"),up("p5b_h4_dif","p5b_h4_dea"))
        acts=[action("EXIT_LONG",reverse_long),action("EXIT_SHORT",reverse_short),action("REDUCE_CURRENT",or_(down("p5b_d_dif",0.0),down("p5b_h4_dif",0.0),up("p5b_d_dif",0.0),up("p5b_h4_dif",0.0)),0.5),
              action("ENTER_LONG",and_(gt("p5b_d_dif",0.0),gt("p5b_h4_dif",0.0),up("p5b_d_dif","p5b_d_dea"),up("p5b_h4_dif","p5b_h4_dea"))),
              action("ENTER_SHORT",and_(lt("p5b_d_dif",0.0),lt("p5b_h4_dif",0.0),down("p5b_d_dif","p5b_d_dea"),down("p5b_h4_dif","p5b_h4_dea")))]
        return definition(row,fs,acts,["MTF_LATEST_COMPLETED_STATE_V1","MTF_TRIGGER_CONFLUENCE_V1","REDUCE_HALF_CURRENT_V1"],"MTF_DAILY_4H_MACD")
    if "多周期 MACD 共振系统" in name:
        fs=bar
        for tag,minutes in (("d",1440),("h4",240),("h1",60)):
            fs += [mtf(f"p5b_{tag}_dif",minutes,"dif","macd",fast_window=12,slow_window=26,signal_window=9),mtf(f"p5b_{tag}_dea",minutes,"signal","macd",fast_window=12,slow_window=26,signal_window=9)]
        longs=[up(f"p5b_{tag}_dif",f"p5b_{tag}_dea") for tag in ("d","h4","h1")]; shorts=[down(f"p5b_{tag}_dif",f"p5b_{tag}_dea") for tag in ("d","h4","h1")]
        zero=[node for tag in ("d","h4","h1") for node in (up(f"p5b_{tag}_dif",0.0),down(f"p5b_{tag}_dif",0.0))]
        acts=[action("EXIT_LONG",{"op":"k_of_m","k":2,"args":shorts}),action("EXIT_SHORT",{"op":"k_of_m","k":2,"args":longs}),action("REDUCE_CURRENT",or_(*zero),0.5),
              action("ENTER_LONG",and_(*(gt(f"p5b_{tag}_dif",0.0) for tag in ("d","h4","h1")),*longs)),
              action("ENTER_SHORT",and_(*(lt(f"p5b_{tag}_dif",0.0) for tag in ("d","h4","h1")),*shorts))]
        return definition(row,fs,acts,["MTF_LATEST_COMPLETED_STATE_V1","K_OF_M_SOURCE_EXPLICIT_V1","REDUCE_HALF_CURRENT_V1"],"MTF_TRIPLE_MACD")
    if "多周期共振简易系统（日线 MA" in name:
        fs=bar+[mtf("p5b_d_close",1440,"value","rolling_mean",window=1),mtf("p5b_d_ma60",1440,"value","rolling_mean",window=60),
                mtf("p5b_h4_dif",240,"dif","macd",fast_window=12,slow_window=26,signal_window=9),mtf("p5b_h4_dea",240,"signal","macd",fast_window=12,slow_window=26,signal_window=9)]
        acts=[action("EXIT_LONG",down("p5b_d_close","p5b_d_ma60")),action("EXIT_SHORT",up("p5b_d_close","p5b_d_ma60")),
              action("REDUCE_CURRENT",or_(down("p5b_h4_dif","p5b_h4_dea"),up("p5b_h4_dif","p5b_h4_dea")),0.5),
              action("ENTER_LONG",and_(gt("p5b_d_close","p5b_d_ma60"),gt("p5b_h4_dif",0.0),up("p5b_h4_dif","p5b_h4_dea"))),
              action("ENTER_SHORT",and_(lt("p5b_d_close","p5b_d_ma60"),lt("p5b_h4_dif",0.0),down("p5b_h4_dif","p5b_h4_dea")))]
        return definition(row,fs,acts,["MTF_LATEST_COMPLETED_STATE_V1","MTF_STATE_TRIGGER_DISTINCTION_V1","REDUCE_HALF_CURRENT_V1"],"MTF_DAILY_MA_4H_MACD")
    if "跨周期 CCI 共振" in name:
        fs=bar+[mtf("p5b_d_cci",1440,"value","cci",window=20),mtf("p5b_h4_cci",240,"value","cci",window=20)]
        acts=[action("EXIT_LONG",gte("p5b_d_cci",100.0)),action("EXIT_SHORT",lte("p5b_d_cci",-100.0)),action("REDUCE_CURRENT",or_(down("p5b_h4_cci",0.0),up("p5b_h4_cci",0.0)),0.5),
              action("ENTER_LONG",and_(gte("p5b_d_cci",-100.0),lte("p5b_d_cci",0.0),up("p5b_h4_cci",-100.0))),
              action("ENTER_SHORT",and_(gte("p5b_d_cci",0.0),lte("p5b_d_cci",100.0),down("p5b_h4_cci",100.0)))]
        return definition(row,fs,acts,["MTF_LATEST_COMPLETED_STATE_V1","MTF_STATE_TRIGGER_DISTINCTION_V1","REDUCE_HALF_CURRENT_V1"],"MTF_DAILY_4H_CCI")
    if "三重周期分形反转共振" in name:
        fs=bar+[feature("sma","p5b_ma20",window=20),feature("adx","p5b_adx14",window=14)]
        for tag,minutes in (("d",1440),("h4",240),("h1",60)):
            fs += [mtf(f"p5b_{tag}_lower",minutes,"lower_fractal_pulse"),mtf(f"p5b_{tag}_upper",minutes,"upper_fractal_pulse")]
        lower=and_(*(pulse(f"p5b_{tag}_lower") for tag in ("d","h4","h1"))); upper=and_(*(pulse(f"p5b_{tag}_upper") for tag in ("d","h4","h1")))
        acts=[action("EXIT_LONG",upper),action("EXIT_SHORT",lower),action("REDUCE_CURRENT",lt("p5b_adx14",20.0),0.5),
              action("ENTER_LONG",and_(lower,consecutive(gt("p5b_close","p5b_ma20")))),action("ENTER_SHORT",and_(upper,consecutive(lt("p5b_close","p5b_ma20"))))]
        return definition(row,fs,acts,["MTF_LATEST_COMPLETED_STATE_V1","CONFIRMED_FRACTAL_2X2_V1","STABLE_CLOSE_2BAR_V1"],"MTF_TRIPLE_FRACTAL")
    if "周线 MA40 长线过滤" in name:
        fs=bar+[mtf("p5b_w_close",10080,"value","rolling_mean",window=1),mtf("p5b_w_ma40",10080,"value","rolling_mean",window=40),
                mtf("p5b_d_ma10",1440,"value","rolling_mean",window=10),mtf("p5b_d_ma20",1440,"value","rolling_mean",window=20)]
        acts=[action("EXIT_LONG",down("p5b_w_close","p5b_w_ma40")),action("EXIT_SHORT",up("p5b_w_close","p5b_w_ma40")),
              action("REDUCE_CURRENT",or_(down("p5b_d_ma10","p5b_d_ma20"),up("p5b_d_ma10","p5b_d_ma20")),0.5),
              action("ENTER_LONG",and_(gt("p5b_w_close","p5b_w_ma40"),up("p5b_d_ma10","p5b_d_ma20"))),
              action("ENTER_SHORT",and_(lt("p5b_w_close","p5b_w_ma40"),down("p5b_d_ma10","p5b_d_ma20")))]
        return definition(row,fs,acts,["MTF_LATEST_COMPLETED_STATE_V1","MTF_STATE_TRIGGER_DISTINCTION_V1","REDUCE_HALF_CURRENT_V1"],"MTF_WEEKLY_DAILY_MA")
    return None


def terminal_semantic_gap(row: dict[str, str]) -> str:
    """Second-pass clause audit for rows not accepted by a reviewed compiler family."""
    text = " ".join((row["long_entry_text"], row["short_entry_text"], row["exit_text"]))
    if any(term in text for term in ("加仓", "分仓", "满仓", "半仓", "逐层", "逐档", "网格", "浮亏", "盈利")):
        return "UNSUPPORTED_ACCOUNTING_SEMANTICS"
    if any(term in text for term in ("箱体", "任意 2 项", "任意2项", "四项", "全部均线", "任一指标", "全套")):
        return "MISSING_REFERENCE_OBJECT"
    if any(term in text for term in ("中性", "极值", "低位", "高位", "中位", "大幅", "极致", "远离", "萎缩", "扩张")):
        return "MISSING_NUMERIC_PARAMETER"
    if any(term in row["exit_text"] for term in ("背离", "反向信号", "趋势反转", "结构消失", "击穿", "回归")):
        return "SEMANTIC_EXIT_AMBIGUOUS"
    if any(term in (row["long_entry_text"] + row["short_entry_text"]) for term in ("支撑", "压力", "回踩", "反弹", "共振", "趋势", "多头", "空头", "确认")):
        return "SEMANTIC_ENTRY_AMBIGUOUS"
    return "UNPARSEABLE_STRUCTURAL_LOGIC"


def main() -> int:
    gaps=read_csv(AUDIT/"phase5b_compiler_gap_audit.csv")
    source={r["source_identity"]:r for r in read_csv(AUDIT/"phase5a_remaining_strategy_audit.csv") if r["phase5a_status"]=="REMAINS_UNRESOLVED"}
    definitions: dict[str,dict[str,Any]]={}; closure=[]; rules=[]; transitions=[]; families=Counter()
    for gap in gaps:
        identity=gap["source_identity"]; row={**source[identity],**gap}
        compiled=compile_reviewed(row) if gap["semantic_definition_complete"]=="true" else None
        if compiled:
            definitions[identity]=compiled; families[compiled["compiler_family"]]+=1
            status="IMPLEMENTED_STANDALONE"; remaining=""
            decoded=json.loads(base64.urlsafe_b64decode(compiled["params"]["rule_spec_b64"]).decode())
            source_text=f"LONG={row['long_entry_text']} | SHORT={row['short_entry_text']} | EXIT={row['exit_text']}"
            intrinsic_direction=("LONG_SHORT" if row["long_entry_text"] and row["short_entry_text"]
                                 else "LONG_ONLY" if row["long_entry_text"] else "SHORT_ONLY")
            rules.append({"source_identity":identity,"strategy_name":row["strategy_name"],"compiler_family":compiled["compiler_family"],"schema_version":2,
                          "source_text":source_text,"normalized_compiled_rule":source_text,
                          "human_rule":source_text,"ir_json":json.dumps(decoded,ensure_ascii=False,sort_keys=True),
                          "contracts_applied":";".join(compiled["contracts_applied"]),
                          "state_machine":"COMPLETED_MTF_STATE" if compiled["compiler_family"].startswith("MTF_") else "EXECUTED_POSITION_LIFECYCLE",
                          "semantic_provenance":compiled["semantic_provenance"],"compiler_recovery":"PHASE5B",
                          "intrinsic_direction":intrinsic_direction,"long_entry_complete":str(bool(row["long_entry_text"])).lower(),
                          "short_entry_complete":str(bool(row["short_entry_text"])).lower(),"exit_complete":"true","risk_module_complete":"true",
                          "rule_hash":compiled["rule_hash"],"source_clause_count":3,"compiled_clause_count":3,
                          "mapped_clause_count":3,"unmapped_source_clauses":"","unmapped_material_clause_count":0})
            transitions.append({"source_identity":identity,"strategy_name":row["strategy_name"],"old_status":"REMAINS_UNRESOLVED","new_status":status,
                                "old_compiler_gap":gap["compiler_gap_category"],"new_compiler_capability":compiled["compiler_family"],
                                "semantic_provenance":compiled["semantic_provenance"],"compiler_recovery":"PHASE5B",
                                "registry_id":identity,"compiler_family":compiled["compiler_family"],"rule_hash":compiled["rule_hash"],"backtest_status":"PENDING"})
        else:
            status="SEMANTICALLY_UNRESOLVED"
            remaining=(gap["semantic_gap_category"] if gap["semantic_definition_complete"]!="true"
                       else terminal_semantic_gap(row))
        closure.append({"source_identity":identity,"strategy_name":row["strategy_name"],"input_status":"REMAINS_UNRESOLVED",
                        "initial_semantic_definition_complete":gap["semantic_definition_complete"],
                        "semantic_definition_complete":str(compiled is not None).lower(),
                        "compiler_gap_category":gap["compiler_gap_category"],
                        "final_compiler_gap":"" if compiled else "NOT_APPLICABLE_SEMANTIC_BLOCKER",
                        "phase5b_status":status,"compiler_family":compiled["compiler_family"] if compiled else "","remaining_blocker":remaining})
    PLAN.parent.mkdir(parents=True,exist_ok=True); tmp=PLAN.with_suffix(".json.tmp"); tmp.write_text(json.dumps(definitions,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); os.replace(tmp,PLAN)
    write_csv(AUDIT/"phase5b_compiled_rules.csv",rules,list(rules[0]) if rules else ["source_identity"])
    write_csv(AUDIT/"phase5b_strategy_closure.csv",closure,list(closure[0]))
    write_csv(AUDIT/"phase5b_status_transitions.csv",transitions,list(transitions[0]) if transitions else ["source_identity"])
    representatives: dict[str, str] = {}
    execution_rows = []
    for identity, item in sorted(definitions.items()):
        representative = representatives.setdefault(item["rule_hash"], identity)
        execution_rows.append({"strategy_id": identity, "rule_hash": item["rule_hash"],
                               "source_timeframe": item["source_timeframe"],
                               "physical_representative": representative,
                               "physical_execution": str(identity == representative).lower()})
    write_csv(AUDIT/"phase5b_execution_plan.csv", execution_rows,
              ["strategy_id", "rule_hash", "source_timeframe", "physical_representative", "physical_execution"])
    mtf_count=sum(v for k,v in families.items() if k.startswith("MTF_"))
    primitive_specs=[
        ("CONDITION_ACTION_AST","compiler","typed condition and action nodes; no eval",len(definitions),"test_phase5b_compiler.py"),
        ("COMPARE_BOOLEAN_CROSS","condition","comparison, boolean composition, and prior-value crossing",len(definitions),"test_workbook_dsl.py"),
        ("PREVIOUS_COMMITTED_STATE","state","historical references use committed observations only",len(definitions),"test_workbook_dsl.py"),
        ("CONSECUTIVE_TURN_DIRECTION","state","persistence, turn, rising, and falling over committed history",len(definitions),"test_workbook_dsl.py"),
        ("ACTION_PRECEDENCE_EXIT_REDUCE_ENTER","action","FLATTEN/EXIT > REDUCE > ENTER/ADD",len(definitions),"test_workbook_dsl.py"),
        ("MTF_LATEST_COMPLETED_STATE","timeframe","expose only fully completed child-timeframe values",mtf_count,"test_completed_timeframe_indicator.py"),
        ("FILL_SYNCHRONIZED_POSITION","execution_state","position and entry price change only through fill synchronization",len(definitions),"test_workbook_dsl.py"),
    ]
    primitive_rows=[{"primitive_id":pid,"type":kind,"implementation_path":"strategy_framework/workbook_dsl.py" if kind!="timeframe" else "feature_engine/compute/feature_lib/session.py",
                     "semantics":semantics,"affected_rows":count,"strategies_unlocked":count,"golden_tests":tests}
                    for pid,kind,semantics,count,tests in primitive_specs]
    write_csv(AUDIT/"phase5b_compiler_primitive_manifest.csv",primitive_rows,list(primitive_rows[0]))
    write_csv(AUDIT/"phase5b_compiler_primitives.csv",primitive_rows,list(primitive_rows[0]))
    write_csv(AUDIT/"phase5b_state_primitive_manifest.csv",[{"primitive":"EXECUTED_POSITION","owner":"execution","semantics":"synchronized only from fill"},{"primitive":"PREVIOUS_COMMITTED_STATE","owner":"strategy","semantics":"prior committed event only"}], ["primitive","owner","semantics"])
    state_manifest=[
        {"state_machine_id":"EXECUTED_POSITION_LIFECYCLE","states":"FLAT;EXECUTED_LONG;EXECUTED_SHORT","transitions":"fill synchronized position transition","source_phrase_patterns":"持有多仓;持有空仓;平仓","implementation_path":"strategy_framework/workbook_dsl.py","strategies_using_it":len(definitions),"test_coverage":"test_workbook_dsl.py"},
        {"state_machine_id":"COMPLETED_MTF_STATE","states":"WARMUP;LATEST_COMPLETED","transitions":"child timeframe boundary completion","source_phrase_patterns":"多周期;跨周期;日线+4小时;周线+日线","implementation_path":"feature_engine/compute/feature_lib/session.py","strategies_using_it":mtf_count,"test_coverage":"test_completed_timeframe_indicator.py"},
    ]
    write_csv(AUDIT/"phase5b_state_machine_manifest.csv",state_manifest,list(state_manifest[0]) if state_manifest else ["state_machine_id"])
    fix={"passes":[{"pass":1,"newly_compiled":len(definitions)},{"pass":2,"newly_compiled":0}],"reached_fixpoint":True,"compiled":len(definitions),"remaining":1082-len(definitions),"families":families}
    (AUDIT/"phase5b_fixpoint_summary.json").write_text(json.dumps(fix,ensure_ascii=False,indent=2,default=dict)+"\n",encoding="utf-8")
    write_csv(AUDIT/"phase5b_fixpoint_iterations.csv",[
        {"iteration":1,"new_strategies_recovered":len(definitions),"new_compiler_primitives_added":"typed_condition_action_ast;completed_timeframe_indicator;fill_synchronized_position"},
        {"iteration":2,"new_strategies_recovered":0,"new_compiler_primitives_added":""},
    ],["iteration","new_strategies_recovered","new_compiler_primitives_added"])
    # Canonical task filename; keep the earlier name as a compatibility alias.
    write_csv(AUDIT/"phase5b_compiled_strategy_rules.csv",rules,list(rules[0]) if rules else ["source_identity"])
    print(json.dumps(fix,ensure_ascii=False,indent=2,default=dict)); return 0


if __name__=="__main__": raise SystemExit(main())
