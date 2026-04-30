"""
Аналитика по dump ленты MRKT (feed.json от scraper.py).
Считает объёмы в TON, разрез по типам событий, коллекциям, часам;
эвристика «сработавший ордер» = listing и sale одного подарка с разницей 0–1 c.
Сохраняет графики в каталог (PNG, опционально объединённый PDF) и печатает отчёт в stdout.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

NANO_PER_TON = 1_000_000_000
INSTANT_FILL_MAX_SEC = 1.0


def parse_ts(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    s = str(raw).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def nano_to_ton(n: Any) -> float:
    if n is None:
        return 0.0
    try:
        return float(n) / NANO_PER_TON
    except (TypeError, ValueError):
        return 0.0


def gift_key(row: dict) -> str | None:
    g = row.get("gift")
    if not isinstance(g, dict):
        return None
    gid = g.get("id")
    if gid:
        return str(gid)
    s = g.get("giftIdString")
    if s is not None:
        return str(s)
    if g.get("giftId") is not None:
        return str(g["giftId"])
    return None


def collection_name(row: dict) -> str:
    g = row.get("gift")
    if isinstance(g, dict):
        return (
            g.get("collectionTitle")
            or g.get("collectionName")
            or g.get("title")
            or "(unknown)"
        )
    return str(row.get("collectionName") or "(unknown)")


def load_items(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items")
    if not isinstance(items, list):
        raise SystemExit("В JSON нет массива meta/items в ожидаемом формате.")
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    return meta, items


def percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p / 100.0
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


@dataclass
class InstantFill:
    gift_key: str
    listing_time: datetime
    sale_time: datetime
    delta_sec: float
    sale_ton: float
    collection: str


def compute_instant_fills(rows: list[dict]) -> list[InstantFill]:
    """Сортировка по времени; последний listing по gift_key перезаписывает предыдущий.
    Любая sale снимает ожидание listing для этого подарка (чтобы не цеплять старый листинг)."""
    indexed: list[tuple[datetime, dict]] = []
    for row in rows:
        ts = parse_ts(row.get("date"))
        if ts is None:
            continue
        indexed.append((ts, row))
    indexed.sort(key=lambda x: x[0])

    pending_listing: dict[str, datetime] = {}
    out: list[InstantFill] = []

    for ts, row in indexed:
        gk = gift_key(row)
        if not gk:
            continue
        et = str(row.get("type") or "").lower()

        if et == "listing":
            pending_listing[gk] = ts
            continue

        if et == "sale":
            lt = pending_listing.get(gk)
            if lt is not None:
                delta = (ts - lt).total_seconds()
                if delta >= 0 and delta <= INSTANT_FILL_MAX_SEC:
                    ton = nano_to_ton(row.get("amount"))
                    out.append(
                        InstantFill(
                            gift_key=gk,
                            listing_time=lt,
                            sale_time=ts,
                            delta_sec=delta,
                            sale_ton=ton,
                            collection=collection_name(row),
                        )
                    )
            pending_listing.pop(gk, None)
            continue

    return out


def bucket_floor(dt: datetime, step: timedelta) -> datetime:
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    sec = (dt - epoch).total_seconds()
    step_sec = step.total_seconds()
    floored = int(sec // step_sec) * step_sec
    return epoch + timedelta(seconds=floored)


def setup_matplotlib() -> Any:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "figure.facecolor": "#0f1419",
            "axes.facecolor": "#16202a",
            "axes.edgecolor": "#3d4f60",
            "axes.labelcolor": "#c8d0d8",
            "text.color": "#e6edf3",
            "xtick.color": "#9fb0c0",
            "ytick.color": "#9fb0c0",
            "grid.color": "#2a3847",
            "grid.alpha": 0.6,
            "font.size": 10,
            "axes.titlesize": 12,
        }
    )
    return plt


def main() -> None:
    ap = argparse.ArgumentParser(description="Аналитика MRKT feed.json")
    ap.add_argument(
        "--feed",
        type=Path,
        default=Path(__file__).resolve().parent / "feed.json",
        help="Путь к feed.json",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "analytics_out",
        help="Каталог для PNG / PDF и summary.txt",
    )
    ap.add_argument("--pdf", action="store_true", help="Дополнительно сохранить все графики в один PDF")
    args = ap.parse_args()

    meta, items = load_items(args.feed)
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- Сбор по строкам с временем
    typed_rows: dict[str, list[dict]] = defaultdict(list)
    times_all: list[datetime] = []
    sale_tons: list[float] = []
    sale_by_coll_ton: defaultdict[str, float] = defaultdict(float)
    sale_by_coll_cnt: defaultdict[str, int] = defaultdict(int)
    listing_by_coll: defaultdict[str, int] = defaultdict(int)

    for row in items:
        ts = parse_ts(row.get("date"))
        if ts is None:
            continue
        times_all.append(ts)
        et = str(row.get("type") or "unknown")
        typed_rows[et].append(row)
        coll = collection_name(row)

        if et == "sale":
            t = nano_to_ton(row.get("amount"))
            sale_tons.append(t)
            sale_by_coll_ton[coll] += t
            sale_by_coll_cnt[coll] += 1
        elif et == "listing":
            listing_by_coll[coll] += 1

    type_counts = Counter({k: len(v) for k, v in typed_rows.items()})
    total_events = sum(type_counts.values())
    t_min = min(times_all) if times_all else None
    t_max = max(times_all) if times_all else None

    sale_volume_total = sum(sale_tons)
    sale_count = len(sale_tons)
    sale_sorted = sorted(sale_tons)

    fills = compute_instant_fills(items)
    fill_volume = sum(f.sale_ton for f in fills)
    fill_by_coll: defaultdict[str, float] = defaultdict(float)
    for f in fills:
        fill_by_coll[f.collection] += f.sale_ton

    # --- Текстовый отчёт
    lines: list[str] = []
    lines.append("=== MRKT feed — сводка ===")
    lines.append(f"Файл: {args.feed}")
    if meta:
        lines.append(f"meta.cutoff_utc: {meta.get('cutoff_utc')}")
        lines.append(f"meta.fetched_at_utc: {meta.get('fetched_at_utc')}")
        lines.append(f"meta.row_count: {meta.get('row_count')}")
        lines.append(f"meta.partial: {meta.get('partial')}")
    lines.append(f"Событий с распознанным временем: {total_events}")
    if t_min and t_max:
        lines.append(f"Интервал данных (UTC): {t_min.isoformat()} … {t_max.isoformat()}")
        lines.append(f"Длительность окна: {(t_max - t_min).total_seconds() / 3600:.2f} ч")
    lines.append("")
    lines.append("--- Распределение по типам событий ---")
    for name, c in type_counts.most_common():
        pct = 100.0 * c / total_events if total_events else 0
        lines.append(f"  {name}: {c} ({pct:.1f}%)")
    lines.append("")
    lines.append("--- Смена цены (change_price) ---")
    cp = type_counts.get("change_price", 0)
    lines.append(f"  Событий: {cp}")
    lines.append("")
    lines.append("--- Листинги (listing) ---")
    lines.append(f"  Событий: {type_counts.get('listing', 0)}")
    lines.append("")
    lines.append("--- Продажи (sale) ---")
    lines.append(f"  Количество: {sale_count}")
    lines.append(f"  Объём: {sale_volume_total:,.4f} TON")
    if sale_sorted:
        lines.append(f"  Цена сделки: min {min(sale_sorted):.4f} | p25 {percentile(sale_sorted, 25):.4f} | median {percentile(sale_sorted, 50):.4f} | p75 {percentile(sale_sorted, 75):.4f} | max {max(sale_sorted):.4f} TON")
    lines.append("")
    lines.append("--- Эвристика «ордер» (listing → sale того же gift ≤ 1 с) ---")
    lines.append(f"  Найдено пар: {len(fills)}")
    lines.append(f"  Объём по этим продажам: {fill_volume:,.4f} TON")
    if sale_volume_total > 0:
        lines.append(f"  Доля объёма всех sale: {100.0 * fill_volume / sale_volume_total:.2f}%")
    if fills:
        deltas = [f.delta_sec for f in fills]
        lines.append(f"  Δt listing→sale: min {min(deltas):.4f}s | median {sorted(deltas)[len(deltas)//2]:.4f}s | max {max(deltas):.4f}s")
    lines.append("")
    lines.append("--- Топ коллекций по объёму продаж (TON) ---")
    for coll, vol in sorted(sale_by_coll_ton.items(), key=lambda x: -x[1])[:20]:
        lines.append(f"  {coll}: {vol:,.2f} TON ({sale_by_coll_cnt[coll]} sale)")
    lines.append("")
    lines.append("--- Топ коллекций по числу листингов ---")
    for coll, n in sorted(listing_by_coll.items(), key=lambda x: -x[1])[:15]:
        lines.append(f"  {coll}: {n}")

    report_text = "\n".join(lines)
    print(report_text)
    (out_dir / "summary.txt").write_text(report_text, encoding="utf-8")

    # --- Визуализация
    plt = setup_matplotlib()
    import matplotlib.dates as mdates
    from matplotlib.backends.backend_pdf import PdfPages

    pdf_figs: list[Any] = []

    def save_fig(fig: Any, name: str) -> None:
        p = out_dir / name
        fig.savefig(p, dpi=150, bbox_inches="tight", facecolor="#0f1419")
        pdf_figs.append(fig)
        plt.close(fig)

    # 1) Типы событий
    if type_counts:
        labels = list(type_counts.keys())
        vals = [type_counts[x] for x in labels]
        cmap = plt.get_cmap("tab20")
        colors = [cmap(i % 20) for i in range(len(labels))]
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.barh(labels[::-1], vals[::-1], color=colors[::-1])
        ax.set_xlabel("Количество")
        ax.set_title("События по типам")
        plt.tight_layout()
        save_fig(fig, "01_event_types.png")

    # 2) Лента активности по 5 минут
    if times_all:
        step = timedelta(minutes=5)
        start = bucket_floor(min(times_all), step)
        end = bucket_floor(max(times_all), step) + step
        buckets: defaultdict[datetime, int] = defaultdict(int)
        for t in times_all:
            buckets[bucket_floor(t, step)] += 1
        xs: list[datetime] = []
        ys: list[int] = []
        cur = start
        while cur <= end:
            xs.append(cur)
            ys.append(buckets.get(cur, 0))
            cur += step
        fig, ax = plt.subplots(figsize=(12, 4))
        ax.fill_between(xs, ys, alpha=0.35, color="#3fb950")
        ax.plot(xs, ys, color="#58a6ff", lw=1.2)
        ax.set_title("Активность ленты (все типы), событий за 5 минут")
        ax.set_ylabel("Событий")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        fig.autofmt_xdate()
        plt.tight_layout()
        save_fig(fig, "02_timeline_5m.png")

    # 3) Продажи по часам (UTC): объём + число
    sale_times = [parse_ts(r.get("date")) for r in typed_rows.get("sale", [])]
    sale_times = [t for t in sale_times if t is not None]
    if sale_times:
        step = timedelta(hours=1)
        start = bucket_floor(min(sale_times), step)
        end = bucket_floor(max(sale_times), step) + step
        vol_h: defaultdict[datetime, float] = defaultdict(float)
        cnt_h: defaultdict[datetime, int] = defaultdict(int)
        for r in typed_rows.get("sale", []):
            ts = parse_ts(r.get("date"))
            if ts is None:
                continue
            b = bucket_floor(ts, step)
            vol_h[b] += nano_to_ton(r.get("amount"))
            cnt_h[b] += 1
        xs = []
        v1 = []
        v2 = []
        cur = start
        while cur <= end:
            xs.append(cur)
            v1.append(vol_h.get(cur, 0.0))
            v2.append(cnt_h.get(cur, 0))
            cur += step
        fig, ax1 = plt.subplots(figsize=(12, 5))
        ax2 = ax1.twinx()
        ax1.bar(xs, v1, width=timedelta(minutes=45), color="#d29922", alpha=0.85, label="Объём (TON)")
        ax2.plot(xs, v2, color="#a371f7", lw=2, marker="o", ms=3, label="Число sale")
        ax1.set_ylabel("TON", color="#d29922")
        ax2.set_ylabel("Кол-во sale", color="#a371f7")
        ax1.set_title("Продажи по часам (UTC)")
        ax1.tick_params(axis="y", labelcolor="#d29922")
        ax2.tick_params(axis="y", labelcolor="#a371f7")
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
        fig.autofmt_xdate()
        h1, l1 = ax1.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax1.legend(h1 + h2, l1 + l2, loc="upper left")
        plt.tight_layout()
        save_fig(fig, "03_sales_by_hour.png")

    # 4) Топ коллекций по объёму продаж
    top_n = 18
    if sale_by_coll_ton:
        pairs = sorted(sale_by_coll_ton.items(), key=lambda x: -x[1])[:top_n]
        labels = [p[0][:42] + ("…" if len(p[0]) > 42 else "") for p in pairs]
        vals = [p[1] for p in pairs]
        fig, ax = plt.subplots(figsize=(10, 8))
        ax.barh(labels[::-1], vals[::-1], color="#58a6ff")
        ax.set_xlabel("Объём продаж, TON")
        ax.set_title(f"Топ-{top_n} коллекций по объёму (sale)")
        plt.tight_layout()
        save_fig(fig, "04_top_collections_volume.png")

    # 5) Гистограмма цены sale
    if len(sale_tons) > 2:
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.hist(sale_tons, bins=min(60, max(10, len(sale_tons) // 50)), color="#3fb950", edgecolor="#0f1419", alpha=0.85)
        ax.set_xlabel("Цена сделки, TON")
        ax.set_ylabel("Число продаж")
        ax.set_title("Распределение цен продаж")
        plt.tight_layout()
        save_fig(fig, "05_sale_price_hist.png")

    # 6) Сравнение: все sale vs «мгновенные» пары
    if sale_count:
        fig, ax = plt.subplots(figsize=(8, 5))
        cats = ["Все sale", "listing→sale ≤1с"]
        vols = [sale_volume_total, fill_volume]
        cols = ["#58a6ff", "#f85149"]
        bars = ax.bar(cats, vols, color=cols, width=0.5)
        ax.set_ylabel("TON")
        ax.set_title("Объём продаж: всего vs эвристика ордера")
        for b, v in zip(bars, vols):
            ax.text(b.get_x() + b.get_width() / 2, b.get_height(), f"{v:,.2f}", ha="center", va="bottom", fontsize=10)
        plt.tight_layout()
        save_fig(fig, "06_instant_fill_vs_all.png")

    # 7) Топ коллекций по объёму «мгновенных» продаж
    if fill_by_coll:
        pairs = sorted(fill_by_coll.items(), key=lambda x: -x[1])[:15]
        labels = [p[0][:40] + ("…" if len(p[0]) > 40 else "") for p in pairs]
        vals = [p[1] for p in pairs]
        fig, ax = plt.subplots(figsize=(9, 7))
        ax.barh(labels[::-1], vals[::-1], color="#f85149")
        ax.set_xlabel("TON (только пары listing→sale ≤1с)")
        ax.set_title("Коллекции по объёму «ордерных» продаж")
        plt.tight_layout()
        save_fig(fig, "07_instant_fill_by_collection.png")

    if args.pdf and pdf_figs:
        pdf_path = out_dir / "report_all.pdf"
        with PdfPages(pdf_path) as pdf:
            for fig in pdf_figs:
                pdf.savefig(fig)
        print(f"\nPDF: {pdf_path}")

    print(f"\nГрафики и summary.txt → {out_dir.resolve()}")


if __name__ == "__main__":
    main()
