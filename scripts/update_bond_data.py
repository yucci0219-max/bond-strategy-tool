#!/usr/bin/env python3
"""
抢权配债策略工具 - 每日数据自动更新脚本

功能：
1. 从集思录抓取待发转债数据 → 更新 DATA
2. 从东方财富抓取已过登记日股票次日行情 → 更新 BACKTEST
3. 从东方财富抓取当年上市转债首日溢价 → 更新 LISTED_PREMIUM
4. 正则替换 HTML 中的 JS 变量
5. 保存降级快照到 data/
6. 失败时发送 Server酱 通知

用法: python scripts/update_bond_data.py
环境变量:
  JISILU_COOKIE - 集思录登录Cookie（可选）
  SCKEY         - Server酱推送Key（可选，失败通知用）
"""

import os, re, json, sys, time, logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter

# ═══════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════

CN = ZoneInfo('Asia/Shanghai')
TODAY = datetime.now(CN).strftime('%Y-%m-%d')
NOW = datetime.now(CN).strftime('%Y-%m-%d %H:%M')

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML_FILES = [
    os.path.join(BASE_DIR, 'index.html'),
    os.path.join(BASE_DIR, 'bond-tool-v5.html'),
]
DATA_DIR = os.path.join(BASE_DIR, 'data')

JISILU_URL = 'https://www.jisilu.cn/webapi/cb/pre/'
JISILU_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Referer': 'https://www.jisilu.cn/web/data/cb/pre/',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}

EM_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://quote.eastmoney.com/',
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('bond_update')

# ═══════════════════════════════════════════════════
# HTTP 客户端（带重试）
# ═══════════════════════════════════════════════════

session = requests.Session()
adapter = HTTPAdapter(max_retries=3)
session.mount('https://', adapter)
session.mount('http://', adapter)

# ═══════════════════════════════════════════════════
# 1. 集思录 DATA 抓取
# ═══════════════════════════════════════════════════

def fetch_jisilu():
    """抓取集思录待发转债数据"""
    headers = dict(JISILU_HEADERS)
    cookie = os.getenv('JISILU_COOKIE')
    if cookie:
        headers['Cookie'] = cookie

    log.info('抓取集思录待发转债...')
    r = session.get(JISILU_URL, headers=headers, timeout=30)
    r.raise_for_status()
    j = r.json()

    if j.get('code') != 200:
        raise ValueError(f'集思录返回错误: code={j.get("code")}, msg={j.get("msg")}')

    rows = j.get('data', [])
    if not rows:
        raise ValueError('集思录返回空数据')

    log.info(f'集思录返回 {len(rows)} 条记录')
    return rows


def parse_progress(progress_full):
    """从 progress_full 中提取最新进度和同意注册日期"""
    reg_match = re.search(r'(\d{4}-\d{2}-\d{2})\s+同意注册', progress_full or '')
    reg_date = reg_match.group(1) if reg_match else ''

    # 提取最新进度（最后一行）
    lines = [l.strip() for l in (progress_full or '').split('\n') if l.strip()]
    last_progress = lines[-1] if lines else ''

    # 判断是否已上市委通过
    passed_committee = '上市委通过' in (progress_full or '')

    return {
        'reg_date': reg_date,
        'passed_committee': passed_committee,
        'last_progress': last_progress,
    }


def build_data(jsl_rows):
    """将集思录原始数据转换为 DATA 数组格式。
    筛选条件: 有登记日 或 有同意注册日期 或 已上市委通过（含apply10数据）
    """
    data = []
    for r in jsl_rows:
        prog = parse_progress(r.get('progress_full', ''))

        # 筛选: 有登记日 / 有同意注册日期 / 已上市委通过
        has_record = bool(r.get('record_dt'))
        if not has_record and not prog['reg_date'] and not prog['passed_committee']:
            continue

        try:
            price = round(float(r.get('price', 0)), 2)
            shares = int(r.get('apply10', 0))
        except (ValueError, TypeError):
            continue

        if price <= 0 or shares <= 0:
            continue

        rating = r.get('rating_cd') or '-'
        # 对于上市委通过但无评级的，用空字符串
        if not rating and prog['passed_committee'] and not prog['reg_date']:
            rating = '-'

        data.append([
            r.get('stock_id', ''),
            r.get('stock_nm', ''),
            r.get('bond_nm') or '待定',
            price,
            shares,
            rating,
            r.get('record_dt') or '',
            r.get('apply_date') or '',
            prog['reg_date'],
        ])

    log.info(f'筛选后有效 DATA {len(data)} 条')
    return data


# ═══════════════════════════════════════════════════
# 2. 东方财富 BACKTEST 抓取
# ═══════════════════════════════════════════════════

def em_market(code):
    """判断东方财富市场编号: 1=沪市, 0=深市"""
    return 1 if code.startswith('6') else 0


def fetch_klines(code, beg_yyyymmdd, end_yyyymmdd='20991231', klt=101):
    """获取东方财富股票/转债K线数据"""
    market = em_market(code)
    params = {
        'secid': f'{market}.{code}',
        'fields1': 'f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
        'klt': klt,
        'fqt': 0,
        'beg': beg_yyyymmdd,
        'end': end_yyyymmdd,
    }
    url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get?' + urlencode(params)
    r = session.get(url, headers=EM_HEADERS, timeout=20)
    r.raise_for_status()
    j = r.json()
    return j.get('data', {}).get('klines', [])


def calc_next_day_pct(code, record_dt):
    """计算登记日次日行情百分比（相对登记日收盘）
    东方财富K线日期格式: 2026-06-26（带连字符）
    """
    # 取登记日所在月份的K线
    dt = datetime.strptime(record_dt, '%Y-%m-%d')
    beg = dt.replace(day=1).strftime('%Y%m%d')

    klines = fetch_klines(code, beg)
    if not klines:
        log.warning(f'{code} K线数据为空')
        return None

    # 找登记日所在行（日期格式为 YYYY-MM-DD）
    rec_close = None
    rec_idx = None

    for i, line in enumerate(klines):
        parts = line.split(',')
        date_str = parts[0]  # 格式: 2026-06-26
        if date_str == record_dt:
            rec_close = float(parts[2])  # f53=收盘
            rec_idx = i
            break

    if rec_close is None or rec_idx is None:
        log.warning(f'{code} 未找到登记日 {record_dt} 的K线数据')
        return None

    # 取次日数据
    if rec_idx + 1 >= len(klines):
        log.warning(f'{code} 登记日 {record_dt} 后无次日数据')
        return None

    nxt = klines[rec_idx + 1].split(',')
    nxt_open = float(nxt[1])    # f52=开盘
    nxt_close = float(nxt[2])   # f53=收盘
    nxt_low = float(nxt[4])     # f55=最低

    return {
        'openPct': round((nxt_open / rec_close - 1) * 100, 2),
        'lowPct': round((nxt_low / rec_close - 1) * 100, 2),
        'closePct': round((nxt_close / rec_close - 1) * 100, 2),
    }


def build_backtest(data_rows):
    """为已过登记日的股票构建BACKTEST数据"""
    backtest = {}
    past_date_count = 0

    for row in data_rows:
        code = row[0]
        record_dt = row[6]  # 登记日

        if not record_dt or record_dt > TODAY:
            continue  # 未过登记日，跳过

        past_date_count += 1
        time.sleep(0.3)  # 控制请求频率

        try:
            pct = calc_next_day_pct(code, record_dt)
            if pct:
                backtest[code] = pct
                log.info(f'  {code} 登记日次日: 开{pct["openPct"]}% 低{pct["lowPct"]}% 收{pct["closePct"]}%')
        except Exception as e:
            log.warning(f'  {code} 回测抓取失败: {e}')

    log.info(f'回测完成: {len(backtest)}/{past_date_count} 只成功')
    return backtest


# ═══════════════════════════════════════════════════
# 3. 东方财富 LISTED_PREMIUM 抓取
# ═══════════════════════════════════════════════════

def fetch_listed_bonds(year):
    """获取当年上市转债列表"""
    params = {
        'reportName': 'RPT_BOND_CB_LIST',
        'columns': 'SECURITY_CODE,SECURITY_NAME_ABBR,LISTING_DATE,ISSUE_YEAR',
        'filter': f'(ISSUE_YEAR="{year}")',
        'pageSize': 500,
        'pageNumber': 1,
    }
    url = 'https://datacenter-web.eastmoney.com/api/data/v1/get?' + urlencode(params)
    r = session.get(url, headers=EM_HEADERS, timeout=30)
    r.raise_for_status()
    j = r.json()

    result = j.get('result', {})
    rows = result.get('data', [])
    if not rows:
        log.warning(f'东方财富未返回 {year} 年上市转债数据')
        return []

    # 过滤出有上市日期的
    listed = [r for r in rows if r.get('LISTING_DATE')]
    log.info(f'东方财富返回 {year} 年上市转债 {len(listed)} 只')
    return listed


def bond_market(code):
    """转债市场编号: 11/113开头=沪市(1), 12/123开头=深市(0)"""
    return 1 if code.startswith('11') else 0


def calc_first_day_premium(bond_code, bond_name, list_date):
    """计算转债上市首日收盘溢价（收盘价-100）"""
    list_d = list_date.split()[0].replace('-', '') if ' ' in list_date else list_date.replace('-', '')
    market = bond_market(bond_code)

    klines = fetch_klines(bond_code, list_d, market_override=market)
    if not klines:
        return None

    first_line = klines[0].split(',')
    date_str = first_line[0]
    first_close = float(first_line[2])

    return {
        'date': date_str,  # K线日期已是 YYYY-MM-DD 格式
        'name': bond_name,
        'close': round(first_close - 100, 2),
    }


def fetch_klines(code, beg_yyyymmdd, end_yyyymmdd='20991231', klt=101, market_override=None):
    """获取K线（支持手动指定市场编号）"""
    market = market_override if market_override is not None else em_market(code)
    params = {
        'secid': f'{market}.{code}',
        'fields1': 'f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
        'klt': klt,
        'fqt': 0,
        'beg': beg_yyyymmdd,
        'end': end_yyyymmdd,
    }
    url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get?' + urlencode(params)
    r = session.get(url, headers=EM_HEADERS, timeout=20)
    r.raise_for_status()
    j = r.json()
    return j.get('data', {}).get('klines', [])


def build_listed_premium():
    """构建当年上市转债首日溢价数据"""
    year = TODAY[:4]
    listed = fetch_listed_bonds(year)

    premium_list = []
    for r in listed:
        bond_code = r.get('SECURITY_CODE', '')
        bond_name = r.get('SECURITY_NAME_ABBR', '')
        list_date = r.get('LISTING_DATE', '')

        if not bond_code or not list_date:
            continue

        time.sleep(0.3)

        try:
            item = calc_first_day_premium(bond_code, bond_name, list_date)
            if item and item['close'] > 0:
                premium_list.append(item)
                log.info(f'  {bond_name}({bond_code}) 首日溢价 {item["close"]}%')
        except Exception as e:
            log.warning(f'  {bond_code} 首日溢价抓取失败: {e}')

    log.info(f'上市溢价完成: {len(premium_list)} 只')
    return premium_list


# ═══════════════════════════════════════════════════
# 4. HTML 正则替换
# ═══════════════════════════════════════════════════

def js_value(value):
    """将Python值转换为JS格式的字符串"""
    if isinstance(value, str):
        # 字符串用单引号包裹（HTML中的JS习惯）
        return "'" + value.replace("'", "\\'") + "'"
    elif isinstance(value, (int, float)):
        if isinstance(value, float) and value == int(value):
            return str(int(value))
        return str(value)
    elif isinstance(value, list):
        # 递归处理列表
        items = ',\n'.join(js_value(item) for item in value)
        # DATA数组每行一个子数组，需要特殊格式化
        if value and isinstance(value[0], list):
            lines = []
            for row in value:
                lines.append('[' + ','.join(js_value(v) for v in row) + ']')
            return '[' + '\n'.join(lines) + ']'
        return '[' + items + ']'
    elif isinstance(value, dict):
        # BACKTEST对象格式
        entries = []
        for k, v in value.items():
            entries.append(f'{js_value(k)}:{js_value_item(v)}')
        return '{' + ','.join(entries) + '}'
    elif isinstance(value, bool):
        return 'true' if value else 'false'
    elif value is None:
        return 'null'
    else:
        return json.dumps(value, ensure_ascii=False)


def js_value_item(value):
    """将单个值转为JS格式（用于对象内）"""
    if isinstance(value, dict):
        entries = []
        for k, v in value.items():
            entries.append(f'{k}:{js_value(v)}')
        return '{' + ','.join(entries) + '}'
    return js_value(value)


def replace_var(content, var_name, new_value_str):
    """用正则替换HTML中的JS变量定义"""
    # 匹配 var VARNAME = ... ; 后面紧跟换行或var/function
    pattern = rf"var\s+{re.escape(var_name)}\s*=\s*.*?;\s*(?=\n|var\s|function\s|</script>)"
    replacement = f"var {var_name} = {new_value_str};"
    result, count = re.subn(pattern, replacement, content, count=1, flags=re.DOTALL)
    if count == 0:
        log.warning(f'未找到变量 {var_name}，跳过替换')
    else:
        log.info(f'替换变量 {var_name} 成功')
    return result


def format_data_js(data):
    """将DATA数组格式化为紧凑的JS格式（保留原HTML中的注释风格）"""
    # 按状态分类
    past_date = [r for r in data if r[6] and r[6] <= TODAY]
    upcoming = [r for r in data if r[6] and r[6] > TODAY]
    yian = [r for r in data if not r[6]]

    # 将每个子数组格式化为带逗号分隔的行
    items = []

    if past_date:
        items.append(f'// 已公布登记日（登记日已过）- {len(past_date)}只')
        for row in past_date:
            items.append('[' + ','.join(js_value(v) for v in row) + ']')

    if upcoming:
        items.append(f'// 即将操作（登记日尚未到）- {len(upcoming)}只')
        for row in upcoming:
            items.append('[' + ','.join(js_value(v) for v in row) + ']')

    if yian:
        items.append(f'// 预案（未公布登记日）- {len(yian)}只')
        for row in yian:
            items.append('[' + ','.join(js_value(v) for v in row) + ']')

    # 注释行不需要逗号，数组行之间需要逗号
    # 构建最终字符串：每个数组行后面加逗号（最后一行不加），注释行不加
    result_lines = []
    array_lines = [i for i in items if not i.startswith('//')]
    comment_lines = [i for i in items if i.startswith('//')]

    # 重新组合：注释行直接输出，数组行用逗号分隔
    output = []
    for item in items:
        if item.startswith('//'):
            output.append(item)
        elif item == array_lines[-1]:
            output.append(item)  # 最后一行不加逗号
        else:
            output.append(item + ',')  # 其他数组行加逗号

    return '[' + '\n'.join(output) + ']'


def format_backtest_js(backtest):
    """将BACKTEST对象格式化为JS格式"""
    entries = []
    for code, pct in backtest.items():
        entries.append(f"'{code}':{{openPct:{pct['openPct']},lowPct:{pct['lowPct']},closePct:{pct['closePct']}}}")
    return '{' + ','.join(entries) + '}'


def format_listed_premium_js(premium_list):
    """将LISTED_PREMIUM数组格式化为JS格式"""
    items = []
    for item in premium_list:
        items.append(f"{{date:'{item['date']}',name:'{item['name']}',close:{item['close']}}}")
    return '[' + ','.join(items) + ']'


def update_html_files(data=None, backtest=None, listed_premium=None):
    """更新HTML文件中的JS变量。
    None 表示不替换该变量（保留原值）。
    空列表/空字典 表示确实数据为空，需要替换为空。
    """
    for path in HTML_FILES:
        if not os.path.exists(path):
            log.warning(f'文件不存在: {path}')
            continue

        text = open(path, 'r', encoding='utf-8').read()

        # DATA_UPDATE_TIME 和 TODAY 总是更新
        text = replace_var(text, 'DATA_UPDATE_TIME', js_value(NOW))
        text = replace_var(text, 'TODAY', js_value(TODAY))

        # 只有非None时才替换（None表示抓取失败且无快照，保留原值）
        if data is not None:
            text = replace_var(text, 'DATA', format_data_js(data))
        else:
            log.warning('DATA保留原值（无新数据也无快照）')

        if backtest is not None:
            text = replace_var(text, 'BACKTEST', format_backtest_js(backtest))
        else:
            log.warning('BACKTEST保留原值（无新数据也无快照）')

        if listed_premium is not None:
            text = replace_var(text, 'LISTED_PREMIUM', format_listed_premium_js(listed_premium))
        else:
            log.warning('LISTED_PREMIUM保留原值（无新数据也无快照）')

        open(path, 'w', encoding='utf-8').write(text)
        log.info(f'已更新: {path}')


# ═══════════════════════════════════════════════════
# 5. 降级快照
# ═══════════════════════════════════════════════════

def save_snapshot(name, data):
    """保存降级快照到data目录"""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f'last_{name}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'update_time': NOW, 'data': data}, f, ensure_ascii=False, indent=2)
    log.info(f'快照已保存: {path}')


def load_snapshot(name):
    """加载降级快照"""
    path = os.path.join(DATA_DIR, f'last_{name}.json')
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        j = json.load(f)
    log.info(f'加载快照: {path} (更新时间: {j.get("update_time")})')
    return j.get('data')


# ═══════════════════════════════════════════════════
# 6. Server酱通知
# ═══════════════════════════════════════════════════

def send_serverchan(title, desp):
    """发送Server酱通知"""
    sckey = os.getenv('SCKEY')
    if not sckey:
        log.info('未配置SCKEY，跳过Server酱通知')
        return

    url = f'https://sctapi.ftqq.com/{sckey}.send'
    try:
        r = session.post(url, data={'title': title, 'desp': desp}, timeout=10)
        log.info(f'Server酱通知已发送: {r.status_code}')
    except Exception as e:
        log.warning(f'Server酱通知失败: {e}')


# ═══════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════

def main():
    log.info(f'═══ 开始每日数据更新 ({NOW}) ═══')

    errors = []

    # 1. 集思录 DATA
    data = None  # None=不替换，空列表=替换为空
    try:
        jsl_rows = fetch_jisilu()
        data = build_data(jsl_rows)
        save_snapshot('jisilu', data)
    except Exception as e:
        log.error(f'集思录抓取失败: {e}')
        errors.append(f'集思录: {e}')
        data = load_snapshot('jisilu')  # 快照存在则为列表，不存在则为None

    # 2. BACKTEST
    backtest = None
    if data:  # 只有DATA可用时才尝试回测
        try:
            backtest = build_backtest(data)
            save_snapshot('backtest', backtest)
        except Exception as e:
            log.error(f'回测数据构建失败: {e}')
            errors.append(f'回测: {e}')
            backtest = load_snapshot('backtest')

    # 3. LISTED_PREMIUM
    listed_premium = None
    try:
        listed_premium = build_listed_premium()
        save_snapshot('listed_premium', listed_premium)
    except Exception as e:
        log.error(f'上市溢价抓取失败: {e}')
        errors.append(f'上市溢价: {e}')
        listed_premium = load_snapshot('listed_premium')

    # 4. 更新HTML
    # 至少有一个数据源成功或有快照才更新
    if data is None and backtest is None and listed_premium is None:
        log.error('全部数据不可用且无快照，跳过HTML更新')
        send_serverchan('抢权配债数据更新失败', '全部数据源不可用且无历史快照，请检查接口状态。')
        sys.exit(1)

    try:
        update_html_files(data=data, backtest=backtest, listed_premium=listed_premium)
    except Exception as e:
        log.error(f'HTML更新失败: {e}')
        errors.append(f'HTML更新: {e}')
        send_serverchan('抢权配债数据更新失败', f'HTML更新失败: {e}')
        sys.exit(1)

    # 5. 结果汇总
    if errors:
        err_msg = '\n'.join(errors)
        log.warning(f'部分步骤失败: {err_msg}')
        send_serverchan('抢权配债数据部分更新', f'部分数据源失败，已使用快照补位。\n失败项:\n{err_msg}')
    else:
        log.info('═══ 全部更新成功 ═══')

    data_count = len(data) if data else 0
    bt_count = len(backtest) if backtest else 0
    lp_count = len(listed_premium) if listed_premium else 0
    log.info(f'DATA: {data_count}只 | BACKTEST: {bt_count}只 | LISTED_PREMIUM: {lp_count}只')


if __name__ == '__main__':
    main()
