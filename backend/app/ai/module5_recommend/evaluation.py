"""Module 5 — Honesty harness (built before any model exists).

This is the referee. It defines the temporal split, the leakage-safe place
stats, the baselines the model must beat, the analytic oracle (floor / ceilings
/ weather budget), and the paired bootstrap that turns "beats the baseline" into
"beats the baseline with 95% CI lower bound above zero".

Everything here runs with NO model present. A model is judged by dropping its
per-event correctness vector into the same bootstrap.

Key leakage guard: place stats (visit_frequency, avg_stay_time) are computed by
clustering the TRAINING WINDOW ONLY, then frozen and reused for train and test.
Because the generator never emits stats, this is the only path they can come
from — future visits physically cannot reach a test feature.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd

from app.ai.module1_behavior.data_preprocessing import preprocess_gps
from app.ai.module1_behavior.place_clustering import cluster_places

from .data_source import BASE_DATE, PLACES, PLACE_KEYS, DataSource, DecisionEvent
from .recommendation_generation import haversine_km

# 70/30 temporal cut: train = days 1-63 (index 0-62), test = days 64-90.
DEFAULT_CUT_DAY = 63
BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 2024


# ---------------------------------------------------------------------------
# Split (train past / test future, no shuffle)
# ---------------------------------------------------------------------------

@dataclass
class Split:
    train_events: list[DecisionEvent]
    test_events: list[DecisionEvent]
    train_gps: list[dict]        # only the train window feeds clustering


def temporal_split(source: DataSource, cut_day: int = DEFAULT_CUT_DAY) -> Split:
    events = source.decision_events()
    gps = source.raw_gps()
    cut_ts = BASE_DATE + timedelta(days=cut_day)
    return Split(
        train_events=[e for e in events if e.day_index < cut_day],
        test_events=[e for e in events if e.day_index >= cut_day],
        train_gps=[g for g in gps if g["timestamp"] < cut_ts],
    )


# ---------------------------------------------------------------------------
# Frozen place stats (leakage guard) + label mapping
# ---------------------------------------------------------------------------

@dataclass
class FrozenPlaces:
    places: list[dict]                 # cluster dicts from the TRAIN window only
    key_to_cluster: dict[str, int]     # semantic place key -> cluster_id
    cluster_to_key: dict[int, str]
    fallback_events: int               # #events whose chosen place wasn't a cluster


def _nearest_key(lat: float, lng: float) -> tuple[str, float]:
    best, best_m = None, 1e9
    for key, (plat, plng) in PLACES.items():
        m = haversine_km(lat, lng, plat, plng) * 1000
        if m < best_m:
            best, best_m = key, m
    return best, best_m


def freeze_place_stats(split: Split) -> FrozenPlaces:
    """Cluster the training window and map each cluster to a semantic place."""
    df = pd.DataFrame(split.train_gps)
    df = preprocess_gps(df)
    places = cluster_places(df)

    key_to_cluster: dict[str, int] = {}
    cluster_to_key: dict[int, str] = {}
    for p in places:
        key, _ = _nearest_key(p["latitude"], p["longitude"])
        cluster_to_key[p["cluster_id"]] = key
        # if two clusters map to the same key, keep the more-visited one
        if key not in key_to_cluster or p["visit_frequency"] > _freq(places, key_to_cluster[key]):
            key_to_cluster[key] = p["cluster_id"]
    return FrozenPlaces(places, key_to_cluster, cluster_to_key, fallback_events=0)


def _freq(places: list[dict], cluster_id: int) -> int:
    for p in places:
        if p["cluster_id"] == cluster_id:
            return p["visit_frequency"]
    return 0


def label_of(event: DecisionEvent, frozen: FrozenPlaces) -> int:
    """The cluster_id the event's chosen place maps to (nearest-cluster fallback)."""
    if event.chosen_place_key in frozen.key_to_cluster:
        return frozen.key_to_cluster[event.chosen_place_key]
    # chosen place didn't form a cluster in the train window -> nearest known cluster
    best, best_m = None, 1e9
    for p in frozen.places:
        m = haversine_km(event.chosen_lat, event.chosen_lng, p["latitude"], p["longitude"]) * 1000
        if m < best_m:
            best, best_m = p["cluster_id"], m
    return best


def labels_for(events: list[DecisionEvent], frozen: FrozenPlaces) -> tuple[list[int], int]:
    fallback = sum(1 for e in events if e.chosen_place_key not in frozen.key_to_cluster)
    return [label_of(e, frozen) for e in events], fallback


# ---------------------------------------------------------------------------
# Baselines (no situational reasoning)
# ---------------------------------------------------------------------------

def majority_cluster(frozen: FrozenPlaces) -> int:
    """The single most-visited place — the 'learned nothing' predictor."""
    return max(frozen.places, key=lambda p: p["visit_frequency"])["cluster_id"]


def contextblind_ranking(frozen: FrozenPlaces) -> list[int]:
    """Rank places by popularity only: freq + familiarity, no time/weather/distance.

    Weights mirror the current rule-based blend's freq:familiarity ratio (0.45:0.20).
    Constant across all events by construction — that's the point.
    """
    max_f = max((p["visit_frequency"] for p in frozen.places), default=0) or 1
    max_s = max((p["avg_stay_time"] for p in frozen.places), default=0.0) or 1.0
    scored = [
        (0.7 * p["visit_frequency"] / max_f + 0.3 * p["avg_stay_time"] / max_s, p["cluster_id"])
        for p in frozen.places
    ]
    return [cid for _, cid in sorted(scored, reverse=True)]


# ---------------------------------------------------------------------------
# Metrics + paired bootstrap
# ---------------------------------------------------------------------------

def top1_correct(preds: list[int], labels: list[int]) -> np.ndarray:
    return np.array([1.0 if p == y else 0.0 for p, y in zip(preds, labels)])


def recall_at_k_correct(ranking: list[int], labels: list[int], k: int = 2) -> np.ndarray:
    topk = set(ranking[:k])
    return np.array([1.0 if y in topk else 0.0 for y in labels])


def bootstrap_ci(correct: np.ndarray, n: int = BOOTSTRAP_N, seed: int = BOOTSTRAP_SEED):
    rng = np.random.RandomState(seed)
    m = len(correct)
    means = [correct[rng.randint(0, m, m)].mean() for _ in range(n)]
    return float(correct.mean()), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def paired_bootstrap_delta(correct_a: np.ndarray, correct_b: np.ndarray,
                           n: int = BOOTSTRAP_N, seed: int = BOOTSTRAP_SEED):
    """CI of (a - b) resampling the SAME event indices for both — valid for deltas."""
    rng = np.random.RandomState(seed)
    m = len(correct_a)
    diffs = []
    for _ in range(n):
        idx = rng.randint(0, m, m)
        diffs.append(correct_a[idx].mean() - correct_b[idx].mean())
    return float((correct_a - correct_b).mean()), float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))


# ---------------------------------------------------------------------------
# Analytic oracle (computed from the generator's truth, before any training)
# ---------------------------------------------------------------------------

def analytic_oracle(source: DataSource, test_events: list[DecisionEvent]) -> dict:
    """Floor/ceilings implied by the generative process, evaluated on test contexts.

    Bayes-aware ceiling : optimal predictor that knows slot+weekend+weather.
    Bayes-blind ceiling : optimal predictor that knows slot+weekend but must
                          marginalize weather out -> the weather budget is the gap.
    """
    all_events = source.decision_events()

    # P(weather | slot) empirically over the whole simulated series.
    weather_given_slot: dict[str, Counter] = defaultdict(Counter)
    for e in all_events:
        weather_given_slot[e.slot][e.weather_bucket] += 1
    p_w_given_slot = {
        slot: {w: c / sum(cnt.values()) for w, c in cnt.items()}
        for slot, cnt in weather_given_slot.items()
    }

    # Weather-blind optimum a*(slot, weekend): argmax over places of the
    # weather-marginalized choice probability.
    blind_choice: dict[tuple[str, bool], str] = {}
    for slot in {e.slot for e in all_events}:
        for wknd in (False, True):
            marg = {k: 0.0 for k in PLACE_KEYS}
            for w, pw in p_w_given_slot[slot].items():
                dist = source.effective_distribution(slot, wknd, w)
                for k in PLACE_KEYS:
                    marg[k] += pw * dist[k]
            blind_choice[(slot, wknd)] = max(marg, key=marg.get)

    aware_probs, blind_probs = [], []
    for e in test_events:
        dist = source.effective_distribution(e.slot, e.is_weekend, e.weather_bucket)
        aware_probs.append(max(dist.values()))                    # know weather -> pick argmax
        blind_probs.append(dist[blind_choice[(e.slot, e.is_weekend)]])  # forced weather-blind pick

    bayes_aware = float(np.mean(aware_probs))
    bayes_blind = float(np.mean(blind_probs))
    return {
        "bayes_aware": bayes_aware,
        "bayes_blind": bayes_blind,
        "weather_budget": bayes_aware - bayes_blind,
    }


# ---------------------------------------------------------------------------
# Report (baselines + oracle, no model)
# ---------------------------------------------------------------------------

def build_report(source: DataSource, cut_day: int = DEFAULT_CUT_DAY) -> dict:
    split = temporal_split(source, cut_day)
    frozen = freeze_place_stats(split)
    labels, fallback = labels_for(split.test_events, frozen)

    maj = majority_cluster(frozen)
    maj_correct = top1_correct([maj] * len(labels), labels)

    cb_rank = contextblind_ranking(frozen)
    cb_pred = [cb_rank[0]] * len(labels)
    cb_correct = top1_correct(cb_pred, labels)
    cb_recall2 = recall_at_k_correct(cb_rank, labels, k=2)

    oracle = analytic_oracle(source, split.test_events)

    return {
        "n_train": len(split.train_events),
        "n_test": len(split.test_events),
        "fallback_events": fallback,
        "frozen": frozen,
        "labels": labels,
        "majority": {"cluster": maj, "correct": maj_correct,
                     "ci": bootstrap_ci(maj_correct)},
        "context_blind": {"pred": cb_pred, "correct": cb_correct,
                          "ci": bootstrap_ci(cb_correct),
                          "recall2": bootstrap_ci(cb_recall2),
                          "vs_majority": paired_bootstrap_delta(cb_correct, maj_correct)},
        "oracle": oracle,
    }


def print_report(source: DataSource, cut_day: int = DEFAULT_CUT_DAY) -> dict:
    r = build_report(source, cut_day)
    frozen = r["frozen"]

    print("=" * 74)
    print("PHASE B — HONESTY HARNESS (baselines + oracle, NO MODEL)")
    print("SIMULATED DATA — validates machinery, not real-patient accuracy")
    print("=" * 74)

    print(f"\nTemporal split (no shuffle): train={r['n_train']} events "
          f"(days 1-{cut_day}), test={r['n_test']} events (days {cut_day+1}-90)")
    print(f"Place stats frozen from TRAIN window only (leakage guard).")
    print(f"  frozen clusters: {len(frozen.places)}  mapping "
          + ", ".join(f"{k}->{v}" for k, v in sorted(frozen.key_to_cluster.items())))
    print(f"  test events using nearest-cluster fallback: {r['fallback_events']}")

    lab = Counter(r["labels"])
    print(f"  test label spread by cluster: "
          + ", ".join(f"{frozen.cluster_to_key.get(c,c)}={n}" for c, n in lab.most_common()))

    def fmt(ci):
        m, lo, hi = ci
        return f"{m:5.1%}  [{lo:5.1%}, {hi:5.1%}]"

    o = r["oracle"]
    print("\n" + "-" * 74)
    print(f"{'RESULTS LADDER (test top-1)':<34}{'accuracy':<14}{'95% CI'}")
    print("-" * 74)
    print(f"{'majority-class floor':<34}{fmt(r['majority']['ci'])}")
    print(f"{'context-blind (freq+familiarity)':<34}{fmt(r['context_blind']['ci'])}")
    print(f"{'  context-blind recall@2':<34}{fmt(r['context_blind']['recall2'])}")
    print(f"{'--- model rows: Phase C ---':<34}")
    print("-" * 74)
    print(f"{'Bayes ceiling, weather-blind':<34}{o['bayes_blind']:5.1%}   (analytic)")
    print(f"{'Bayes ceiling, weather-aware':<34}{o['bayes_aware']:5.1%}   (analytic)")
    print(f"{'>> weather budget (max lift)':<34}{o['weather_budget']:+5.1%}   "
          f"(target the ablation delta should approach)")
    print("-" * 74)

    d, lo, hi = r["context_blind"]["vs_majority"]
    print(f"\nSanity — context-blind minus majority: {d:+.1%} [{lo:+.1%}, {hi:+.1%}]")
    print("  (expected ~0: when Home dominates both freq & familiarity, popularity-")
    print("   weighting can't beat pure majority. The MODEL must beat BOTH using")
    print("   situational context — that's the pass bar.)")

    print(f"\nPASS BAR (Phase C): full model top-1 beats BOTH majority-class and")
    print(f"context-blind with paired 95% CI lower bound > 0. Else -> keep rule-based.")
    return r


# ---------------------------------------------------------------------------
# Phase C — model evaluation (drops model correctness vectors into the same
# referee built above; nothing about the split/baselines/oracle changes)
# ---------------------------------------------------------------------------

def ranker_correct(ranker, events: list[DecisionEvent], labels: list[int]):
    """Per-event top-1 and recall@2 correctness vectors for a fitted ranker."""
    top1, rec2 = [], []
    for e, y in zip(events, labels):
        ranking = [cid for cid, _ in ranker.rank_event(e)]
        top1.append(1.0 if ranking[0] == y else 0.0)
        rec2.append(1.0 if y in ranking[:2] else 0.0)
    return np.array(top1), np.array(rec2)


def permutation_importance_weather(ranker, events: list[DecisionEvent],
                                   labels: list[int], n_repeats: int = 20,
                                   seed: int = 77) -> float:
    """Event-level permutation importance: shuffle which weather each test event
    sees (a real bucket, just the wrong event's) and measure the top-1 drop.
    Independent cross-check on the remove-and-retrain ablation."""
    from dataclasses import replace
    rng = np.random.RandomState(seed)
    base, _ = ranker_correct(ranker, events, labels)
    drops = []
    weathers = [e.weather_bucket for e in events]
    for _ in range(n_repeats):
        idx = rng.permutation(len(events))
        shuffled = [replace(e, weather_bucket=weathers[j])
                    for e, j in zip(events, idx)]
        perm, _ = ranker_correct(ranker, shuffled, labels)
        drops.append(base.mean() - perm.mean())
    return float(np.mean(drops))


def _fit_ranker(kind: str, use_weather: bool, split: Split, frozen: FrozenPlaces,
                provenance: dict):
    from .ranker import Module5Ranker
    return Module5Ranker(kind=kind, use_weather=use_weather).fit(
        split.train_events, frozen.places,
        label_fn=lambda e: label_of(e, frozen),
        provenance=provenance,
    )


def expanding_window_deltas(source: DataSource,
                            cuts: tuple[int, ...] = (45, 54, 63, 72, 81),
                            window: int = 9) -> dict:
    """Reserve rigor for borderline CIs (locked in design review, Q4).

    Rolling-origin evaluation: for each cut, train on days < cut, test on the
    next ``window`` days, refreezing place stats per fold. Per-event paired
    correctness vectors are pooled across folds (~5x the single-holdout test N)
    and the paired bootstrap runs on the pool. Used when a single-holdout delta
    CI straddles zero — here, the weather ablation.
    """
    pool_full, pool_time, pool_maj = [], [], []
    for cut in cuts:
        split = temporal_split(source, cut)
        split.test_events = [e for e in split.test_events if e.day_index < cut + window]
        if not split.test_events:
            continue
        frozen = freeze_place_stats(split)
        labels, _ = labels_for(split.test_events, frozen)
        prov = {"data": "SIMULATED", "fold_cut": cut}

        full = _fit_ranker("histgbt", True, split, frozen, prov)
        time_only = _fit_ranker("histgbt", False, split, frozen, prov)

        f_t1, _ = ranker_correct(full, split.test_events, labels)
        t_t1, _ = ranker_correct(time_only, split.test_events, labels)
        maj = majority_cluster(frozen)
        m_t1 = top1_correct([maj] * len(labels), labels)

        pool_full.append(f_t1)
        pool_time.append(t_t1)
        pool_maj.append(m_t1)

    full_v = np.concatenate(pool_full)
    time_v = np.concatenate(pool_time)
    maj_v = np.concatenate(pool_maj)
    return {
        "n_pooled": len(full_v),
        "full_t1": bootstrap_ci(full_v),
        "weather": paired_bootstrap_delta(full_v, time_v),
        "full_vs_majority": paired_bootstrap_delta(full_v, maj_v),
    }


def final_honesty_report(source: DataSource, cut_day: int = DEFAULT_CUT_DAY) -> dict:
    """The go/no-go table: baselines + oracle + model rows + ablation + verdict."""
    r = build_report(source, cut_day)
    split = temporal_split(source, cut_day)
    frozen = r["frozen"]
    labels = r["labels"]
    prov = {"data": "SIMULATED (MockDataSource)", "cut_day": cut_day}

    logistic = _fit_ranker("logistic", True, split, frozen, prov)
    gbt_time = _fit_ranker("histgbt", False, split, frozen, prov)
    gbt_full = _fit_ranker("histgbt", True, split, frozen, prov)

    log_t1, log_r2 = ranker_correct(logistic, split.test_events, labels)
    time_t1, time_r2 = ranker_correct(gbt_time, split.test_events, labels)
    full_t1, full_r2 = ranker_correct(gbt_full, split.test_events, labels)

    # train-vs-test gap (overfitting check) for the deliverable
    train_labels, _ = labels_for(split.train_events, frozen)
    train_t1, _ = ranker_correct(gbt_full, split.train_events, train_labels)

    maj_c = r["majority"]["correct"]
    cb_c = r["context_blind"]["correct"]

    out = {
        "base": r,
        "models": {
            "logistic": {"t1": bootstrap_ci(log_t1), "r2": bootstrap_ci(log_r2)},
            "gbt_time": {"t1": bootstrap_ci(time_t1), "r2": bootstrap_ci(time_r2)},
            "gbt_full": {"t1": bootstrap_ci(full_t1), "r2": bootstrap_ci(full_r2)},
        },
        "gbt_full_iter": gbt_full.model.max_iter,
        "train_top1": float(train_t1.mean()),
        "deltas": {
            "full_vs_majority": paired_bootstrap_delta(full_t1, maj_c),
            "full_vs_contextblind": paired_bootstrap_delta(full_t1, cb_c),
            "weather": paired_bootstrap_delta(full_t1, time_t1),
        },
        "perm_importance_weather": permutation_importance_weather(
            gbt_full, split.test_events, labels),
        "gbt_full_ranker": gbt_full,
    }
    d = out["deltas"]
    out["pass_bar"] = d["full_vs_majority"][1] > 0 and d["full_vs_contextblind"][1] > 0
    out["weather_helps"] = d["weather"][1] > 0
    return out


def print_final_report() -> dict:
    from .data_source import MockDataSource

    src = MockDataSource(n_days=90, seed=42)
    out = final_honesty_report(src)
    r, o = out["base"], out["base"]["oracle"]

    def fmt(ci):
        m, lo, hi = ci
        return f"{m:5.1%}  [{lo:5.1%}, {hi:5.1%}]"

    print("=" * 74)
    print("FINAL HONESTY TABLE — Module 5 learned ranker  (SIMULATED DATA)")
    print("Validates the machinery; real-patient accuracy pending real data.")
    print("=" * 74)
    print(f"\nSplit: train={r['n_train']} / test={r['n_test']} events, temporal, "
          f"place stats frozen to train window.")

    m = out["models"]
    print("\n" + "-" * 74)
    print(f"{'RESULTS LADDER (test top-1)':<36}{'accuracy':<15}{'recall@2'}")
    print("-" * 74)
    print(f"{'majority-class floor':<36}{fmt(r['majority']['ci'])}")
    print(f"{'context-blind (freq+familiarity)':<36}{fmt(r['context_blind']['ci'])}"
          f"   {fmt(r['context_blind']['recall2'])}")
    print(f"{'logistic, full (diagnostic rung)':<36}{fmt(m['logistic']['t1'])}"
          f"   {fmt(m['logistic']['r2'])}")
    print(f"{'HistGBT, time-only (no weather)':<36}{fmt(m['gbt_time']['t1'])}"
          f"   {fmt(m['gbt_time']['r2'])}")
    print(f"{'HistGBT, FULL (deliverable)':<36}{fmt(m['gbt_full']['t1'])}"
          f"   {fmt(m['gbt_full']['r2'])}")
    print("-" * 74)
    print(f"{'Bayes ceiling, weather-blind':<36}{o['bayes_blind']:5.1%}   (analytic)")
    print(f"{'Bayes ceiling, weather-aware':<36}{o['bayes_aware']:5.1%}   (analytic)")
    print("-" * 74)

    d = out["deltas"]
    def fmtd(t):
        mean, lo, hi = t
        return f"{mean:+5.1%}  [{lo:+5.1%}, {hi:+5.1%}]"
    print(f"\nPASS BAR (paired bootstrap deltas, must have CI lower bound > 0):")
    print(f"  full model - majority        : {fmtd(d['full_vs_majority'])}"
          f"   -> {'PASS' if d['full_vs_majority'][1] > 0 else 'FAIL'}")
    print(f"  full model - context-blind   : {fmtd(d['full_vs_contextblind'])}"
          f"   -> {'PASS' if d['full_vs_contextblind'][1] > 0 else 'FAIL'}")
    print(f"\nMODULE-2 DISTINCTNESS (weather ablation, budget {o['weather_budget']:+.1%}):")
    print(f"  full - time-only (retrained) : {fmtd(d['weather'])}"
          f"   -> {'weather helps' if out['weather_helps'] else 'CI includes 0 — NOT PROVEN'}")
    print(f"  permutation importance       : {out['perm_importance_weather']:+.1%} "
          f"top-1 drop when weather shuffled (cross-check)")

    # Reserve rigor (locked Q4): expanding-window CV when a delta CI is borderline.
    if not out["weather_helps"] or d["full_vs_majority"][1] <= 0:
        print(f"\n  Borderline/disagreeing CIs -> running the locked reserve:")
        print(f"  EXPANDING-WINDOW CV (5 folds, pooled paired events)")
        ew = expanding_window_deltas(MockDataSource(n_days=90, seed=42))
        out["expanding_window"] = ew
        print(f"    pooled test events         : {ew['n_pooled']}")
        print(f"    full model top-1           : {fmt(ew['full_t1'])}")
        print(f"    full - majority            : {fmtd(ew['full_vs_majority'])}"
              f"   -> {'PASS' if ew['full_vs_majority'][1] > 0 else 'FAIL'}")
        print(f"    full - time-only (weather) : {fmtd(ew['weather'])}"
              f"   -> {'weather helps (significant)' if ew['weather'][1] > 0 else 'still not significant'}")
        out["weather_helps_ew"] = ew["weather"][1] > 0

    print(f"\nOverfitting check (HistGBT full, max_iter={out['gbt_full_iter']}): "
          f"train top-1 {out['train_top1']:.1%} vs test {m['gbt_full']['t1'][0]:.1%}")

    # Cold-start: the realistic 30-day new-patient regime, reported separately.
    print("\n" + "-" * 74)
    print("COLD-START (separate 30-day run, train 1-21 / test 22-30 — honest check)")
    cs_src = MockDataSource(n_days=30, seed=42)
    cs = final_honesty_report(cs_src, cut_day=21)
    csm, csd = cs["models"], cs["deltas"]
    print(f"  majority floor : {fmt(cs['base']['majority']['ci'])}")
    print(f"  HistGBT full   : {fmt(csm['gbt_full']['t1'])}")
    print(f"  full - majority: {fmtd(csd['full_vs_majority'])}"
          f"   -> {'PASS' if cs['pass_bar'] else 'INCONCLUSIVE on 30 days — revisit with real data'}")
    print("-" * 74)

    verdict = "GENUINE ML — ships as the score_place scorer" if out["pass_bar"] \
        else "LEARNED NOTHING beyond popularity — keep rule-based, say so"
    print(f"\nVERDICT: {verdict}")
    weather_proven = out["weather_helps"] or out.get("weather_helps_ew", False)
    if not weather_proven:
        print("NOTE: weather delta not significant on either the holdout or the")
        print("expanding-window reserve -> Module 5's distinctness from Module 2")
        print("is NOT demonstrated on this data. Stated per design review.")
    elif not out["weather_helps"]:
        print("NOTE: weather delta was borderline on the single holdout but is")
        print("significant under expanding-window CV (the locked reserve).")
    return out


if __name__ == "__main__":
    print_final_report()
