"""供应链映射数据库 — 私有护城河

三张表:
  supply_chain_events  — 催化事件定义
  supply_chain_nodes   — 事件→供应链节点→标的
  validation_log       — 每次预测的命中记录
"""
import sqlite3
from datetime import datetime
from settings import SC_DB_PATH


def _conn() -> sqlite3.Connection:
    SC_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(SC_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS supply_chain_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_name  TEXT NOT NULL UNIQUE,
                category    TEXT NOT NULL,
                narrative   TEXT,
                keywords    TEXT,
                created_at  TEXT
            );
            CREATE TABLE IF NOT EXISTS supply_chain_nodes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id    INTEGER NOT NULL REFERENCES supply_chain_events(id),
                tier        TEXT NOT NULL CHECK(tier IN ('上游','中游','下游','设备','材料','应用','替代')),
                stock_code  TEXT NOT NULL,
                stock_name  TEXT NOT NULL,
                role        TEXT,
                confidence  TEXT NOT NULL DEFAULT 'confirmed' CHECK(confidence IN ('confirmed','speculative')),
                source      TEXT,
                updated_at  TEXT,
                UNIQUE(event_id, stock_code)
            );
            CREATE TABLE IF NOT EXISTS validation_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT NOT NULL,
                event_name  TEXT,
                stock_code  TEXT NOT NULL,
                stock_name  TEXT,
                predicted_dir TEXT,
                actual_chg   REAL,
                volume_ratio REAL,
                flow_signal  TEXT,
                validated    INTEGER DEFAULT 0,
                notes        TEXT
            );
            CREATE TABLE IF NOT EXISTS theme_tracker (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                event_name     TEXT NOT NULL,
                date           TEXT NOT NULL,
                day_n          INTEGER DEFAULT 1,
                confidence     TEXT,
                status         TEXT NOT NULL DEFAULT 'active'
                               CHECK(status IN ('active','weakening','confirmed','dead')),
                mainline_match TEXT,
                fading_match   TEXT,
                emerging_match TEXT,
                notes          TEXT,
                UNIQUE(event_name, date)
            );
            CREATE INDEX IF NOT EXISTS idx_nodes_event ON supply_chain_nodes(event_id);
            CREATE INDEX IF NOT EXISTS idx_nodes_code ON supply_chain_nodes(stock_code);
            CREATE INDEX IF NOT EXISTS idx_vlog_date ON validation_log(date);
            CREATE INDEX IF NOT EXISTS idx_tracker_event ON theme_tracker(event_name);
            CREATE INDEX IF NOT EXISTS idx_tracker_date ON theme_tracker(date);
        """)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def add_event(name: str, category: str, narrative: str = "", keywords: str = ""):
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO supply_chain_events "
            "(event_name, category, narrative, keywords, created_at) "
            "VALUES (?,?,?,?,?)",
            (name, category, narrative, keywords, _now()),
        )


def add_node(event_name: str, tier: str, code: str, name: str,
             role: str = "", confidence: str = "confirmed", source: str = ""):
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM supply_chain_events WHERE event_name = ?", (event_name,)
        ).fetchone()
        if not row:
            raise ValueError(f"事件不存在: {event_name}")
        conn.execute(
            "INSERT OR REPLACE INTO supply_chain_nodes "
            "(event_id, tier, stock_code, stock_name, role, confidence, source, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (row["id"], tier, code, name, role, confidence, source, _now()),
        )


def query_chain(event_name: str = None, category: str = None, code: str = None) -> dict:
    with _conn() as conn:
        if code:
            rows = conn.execute("""
                SELECT e.event_name, e.category, n.tier, n.stock_code,
                       n.stock_name, n.role, n.confidence
                FROM supply_chain_events e
                JOIN supply_chain_nodes n ON e.id = n.event_id
                WHERE n.stock_code = ?
                ORDER BY e.event_name, n.tier
            """, (code,)).fetchall()
        elif event_name:
            rows = conn.execute("""
                SELECT e.event_name, e.category, n.tier, n.stock_code,
                       n.stock_name, n.role, n.confidence
                FROM supply_chain_events e
                JOIN supply_chain_nodes n ON e.id = n.event_id
                WHERE e.event_name LIKE ?
                ORDER BY n.tier
            """, (f"%{event_name}%",)).fetchall()
        elif category:
            rows = conn.execute("""
                SELECT e.event_name, e.category, n.tier, n.stock_code,
                       n.stock_name, n.role, n.confidence
                FROM supply_chain_events e
                JOIN supply_chain_nodes n ON e.id = n.event_id
                WHERE e.category = ?
                ORDER BY e.event_name, n.tier
            """, (category,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT e.event_name, e.category, n.tier, n.stock_code,
                       n.stock_name, n.role, n.confidence
                FROM supply_chain_events e
                JOIN supply_chain_nodes n ON e.id = n.event_id
                ORDER BY e.event_name, n.tier
            """).fetchall()

    result: dict[str, dict] = {}
    for r in rows:
        evt = r["event_name"]
        if evt not in result:
            result[evt] = {"category": r["category"], "nodes": {}}
        tier = r["tier"]
        if tier not in result[evt]["nodes"]:
            result[evt]["nodes"][tier] = []
        result[evt]["nodes"][tier].append({
            "code": r["stock_code"], "name": r["stock_name"],
            "role": r["role"], "confidence": r["confidence"],
        })
    return result


def query_stocks(keyword: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT n.stock_code, n.stock_name, n.tier, n.role, e.event_name
            FROM supply_chain_nodes n
            JOIN supply_chain_events e ON n.event_id = e.id
            WHERE e.event_name LIKE ? OR e.keywords LIKE ?
               OR n.role LIKE ? OR n.tier LIKE ?
            ORDER BY e.event_name, n.tier
        """, (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", f"%{keyword}%")).fetchall()
    return [{"code": r["stock_code"], "name": r["stock_name"],
             "tier": r["tier"], "role": r["role"], "event": r["event_name"]}
            for r in rows]


def list_events() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT e.*, COUNT(n.id) as node_count
            FROM supply_chain_events e
            LEFT JOIN supply_chain_nodes n ON e.id = n.event_id
            GROUP BY e.id ORDER BY e.category, e.event_name
        """).fetchall()
    return [dict(r) for r in rows]


def to_context(event_names: list[str] = None) -> str:
    result = query_chain()
    lines = []
    for evt, data in result.items():
        if event_names and evt not in event_names:
            continue
        lines.append(f"## {evt}（{data['category']}）")
        for tier in ["上游", "中游", "下游", "设备", "材料", "应用"]:
            stocks = data["nodes"].get(tier, [])
            if stocks:
                items = ", ".join(f"{s['name']}({s['code']})" for s in stocks)
                lines.append(f"  {tier}: {items}")
        lines.append("")
    return "\n".join(lines)


def log_validation(date: str, event_name: str, code: str, name: str,
                   predicted_dir: str, actual_chg: float, volume_ratio: float = 0,
                   flow_signal: str = "", validated: int = 0, notes: str = ""):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO validation_log "
            "(date, event_name, stock_code, stock_name, predicted_dir, "
            " actual_chg, volume_ratio, flow_signal, validated, notes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (date, event_name, code, name, predicted_dir, actual_chg,
             volume_ratio, flow_signal, validated, notes),
        )


def validation_stats(days: int = 30) -> dict:
    with _conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM validation_log "
            "WHERE validated != 0 AND date >= date('now', ?)",
            (f"-{days} days",),
        ).fetchone()[0]
        hit = conn.execute(
            "SELECT COUNT(*) FROM validation_log "
            "WHERE validated = 1 AND date >= date('now', ?)",
            (f"-{days} days",),
        ).fetchone()[0]
        by_event = conn.execute("""
            SELECT event_name, COUNT(*) as total,
                   SUM(CASE WHEN validated=1 THEN 1 ELSE 0 END) as hits
            FROM validation_log WHERE validated != 0 AND date >= date('now', ?)
            GROUP BY event_name ORDER BY total DESC
        """, (f"-{days} days",)).fetchall()
    return {
        "total": total,
        "hits": hit,
        "hit_rate": round(hit / total * 100, 1) if total else 0,
        "by_event": [{"event": r["event_name"], "total": r["total"],
                       "hits": r["hits"]} for r in by_event],
    }


# ============================================================
# 题材追踪 — 观察期规则（≥2天缓冲才判定方向）
# ============================================================

def track_theme(event_name: str, date: str, confidence: str = "",
                mainline_match: str = "", fading_match: str = "",
                emerging_match: str = "", notes: str = "") -> dict:
    """记录当日题材观测，返回当前追踪状态。
    规则：首日一律 active；连续 ≥2 天同一方向才变更状态。
    """
    with _conn() as conn:
        prior = conn.execute(
            "SELECT day_n, status, mainline_match, fading_match FROM theme_tracker "
            "WHERE event_name = ? ORDER BY date DESC LIMIT 1",
            (event_name,),
        ).fetchone()

        if prior is None:
            day_n = 1
            status = "active"
        else:
            day_n = prior["day_n"] + 1
            prior_had_mainline = bool(prior["mainline_match"])
            prior_had_fading = bool(prior["fading_match"])
            today_has_mainline = bool(mainline_match)
            today_has_fading = bool(fading_match)

            if today_has_fading and prior_had_fading:
                status = "weakening"
            elif today_has_mainline and prior_had_mainline:
                status = "confirmed"
            else:
                status = "active"

        conn.execute(
            "INSERT OR REPLACE INTO theme_tracker "
            "(event_name, date, day_n, confidence, status, "
            " mainline_match, fading_match, emerging_match, notes) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (event_name, date, day_n, confidence, status,
             mainline_match or "", fading_match or "", emerging_match or "", notes or ""),
        )

    return {"event_name": event_name, "day_n": day_n, "status": status,
            "mainline_match": mainline_match, "fading_match": fading_match}


def theme_status(event_name: str) -> dict | None:
    """查询题材当前追踪状态。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM theme_tracker WHERE event_name = ? "
            "ORDER BY date DESC LIMIT 1", (event_name,),
        ).fetchone()
    return dict(row) if row else None


def list_active_themes() -> list[dict]:
    """列出所有未确认/未死亡的活跃题材。"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT event_name, date, day_n, confidence, status, "
            "mainline_match, fading_match "
            "FROM theme_tracker WHERE status IN ('active','weakening') "
            "ORDER BY date DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ============================================================
# 种子数据
# ============================================================

_SEEDS = [
    ("PCB(IC载板/MSAP)", "硬科技",
     "先进封装推动IC载板需求爆发；MSAP工艺替代传统减成法",
     "IC载板,MSAP,ABF,BT,载板,PCB,HDI"),
    ("PCB(IC载板/MSAP)", "上游", "688300", "联瑞新材", "硅微粉(ABF填料)", "confirmed", ""),
    ("PCB(IC载板/MSAP)", "上游", "603256", "宏和科技", "电子级玻璃布", "confirmed", ""),
    ("PCB(IC载板/MSAP)", "中游", "002916", "深南电路", "IC载板龙头", "confirmed", ""),
    ("PCB(IC载板/MSAP)", "中游", "002384", "东山精密", "FPC/HDI", "confirmed", ""),
    ("PCB(IC载板/MSAP)", "中游", "300476", "胜宏科技", "高密度HDI/载板", "confirmed", ""),
    ("PCB(IC载板/MSAP)", "中游", "688183", "生益电子", "高频高速PCB", "confirmed", ""),
    ("PCB(IC载板/MSAP)", "材料", "603115", "海星股份", "电极箔(PCB上游)", "confirmed", ""),

    ("半导体(先进制程/设备)", "硬科技",
     "国产替代+AI算力驱动半导体景气周期",
     "半导体,芯片,晶圆,光刻,先进封装,chiplet"),
    ("半导体(先进制程/设备)", "上游", "688268", "华特气体", "电子特气", "confirmed", ""),
    ("半导体(先进制程/设备)", "上游", "688300", "联瑞新材", "硅微粉(封装填料)", "confirmed", ""),
    ("半导体(先进制程/设备)", "中游", "688981", "中芯国际", "晶圆代工", "confirmed", ""),
    ("半导体(先进制程/设备)", "中游", "688256", "寒武纪", "AI训练芯片", "confirmed", ""),
    ("半导体(先进制程/设备)", "中游", "688521", "芯原股份", "芯片设计IP", "confirmed", ""),
    ("半导体(先进制程/设备)", "中游", "688525", "佰维存储", "存储芯片模组", "confirmed", ""),
    ("半导体(先进制程/设备)", "中游", "688536", "思瑞浦", "模拟芯片", "confirmed", ""),
    ("半导体(先进制程/设备)", "中游", "688766", "普冉股份", "NOR Flash", "confirmed", ""),
    ("半导体(先进制程/设备)", "替代", "688629", "华丰科技", "连接器(国产替代)", "confirmed", ""),

    ("光模块(CPO/LPO)", "硬科技",
     "AI数据中心800G/1.6T升级，CPO/LPO技术路线竞争",
     "光模块,CPO,LPO,硅光,光通信,光芯片,800G,1.6T"),
    ("光模块(CPO/LPO)", "上游", "300394", "天孚通信", "光器件(FA/隔离器)", "confirmed", ""),
    ("光模块(CPO/LPO)", "上游", "300570", "太辰光", "陶瓷插芯/光纤连接器", "confirmed", ""),
    ("光模块(CPO/LPO)", "上游", "688313", "仕佳光子", "光芯片(AWG/PLC)", "confirmed", ""),
    ("光模块(CPO/LPO)", "上游", "688498", "源杰科技", "光芯片(激光器)", "confirmed", ""),
    ("光模块(CPO/LPO)", "中游", "300502", "新易盛", "光模块龙头(800G)", "confirmed", ""),
    ("光模块(CPO/LPO)", "中游", "300548", "长芯博创", "光模块(AOC)", "confirmed", ""),
    ("光模块(CPO/LPO)", "中游", "301205", "联特科技", "光模块(400G/800G)", "confirmed", ""),
    ("光模块(CPO/LPO)", "中游", "002396", "星网锐捷", "数据中心交换机", "confirmed", ""),

    ("机器人(人形/工业)", "硬科技",
     "特斯拉Optimus量产预期+国内政策加码+AI具身智能",
     "机器人,人形,伺服,减速器,灵巧手,传感器"),
    ("机器人(人形/工业)", "上游", "300503", "昊志机电", "伺服电机/电主轴", "confirmed", ""),
    ("机器人(人形/工业)", "上游", "688661", "和林微纳", "微电机/传感器", "confirmed", ""),
    ("机器人(人形/工业)", "中游", "601727", "上海电气", "工业机器人集成", "confirmed", ""),
    ("机器人(人形/工业)", "中游", "603596", "伯特利", "线控制动(机器人关节)", "speculative", ""),
    ("机器人(人形/工业)", "中游", "002703", "浙江世宝", "转向器(机器人执行器)", "speculative", ""),

    ("固态电池", "新能源",
     "半固态/全固态电池产业化加速，龙头车企装车验证",
     "固态电池,半固态,电解质,硅碳负极,锂电"),
    ("固态电池", "上游", "603026", "石大胜华", "电解液/溶剂", "confirmed", ""),
    ("固态电池", "上游", "688275", "万润新能", "磷酸铁锂正极", "confirmed", ""),
    ("固态电池", "中游", "688388", "嘉元科技", "铜箔(锂电集流体)", "confirmed", ""),
    ("固态电池", "中游", "603950", "长源东谷", "电池精密结构件", "speculative", ""),
    ("固态电池", "下游", "300450", "先导智能", "锂电设备", "confirmed", ""),
    ("固态电池", "材料", "300461", "田中精机", "锂电绕线设备", "speculative", ""),
]


def seed_defaults():
    init_db()
    added_e = added_n = 0
    for row in _SEEDS:
        if len(row) == 4:
            add_event(*row)
            added_e += 1
        else:
            try:
                add_node(*row)
                added_n += 1
            except ValueError:
                pass
    return added_e, added_n
