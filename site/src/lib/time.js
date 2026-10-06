/**
 * 时间：显示成给人读的样子，并按月／周索引（纯逻辑，一行 React 都没有）。
 *
 * 两条纪律：
 *
 * 1. **显示按原样渲染，不做时区换算**。界面上出现的完整时刻只有题卡的 `created_at`
 *    （数据里是 `+08:00`，就是你当时看表看到的那个钟点）。做换算反而会引出两套口径：
 *    重做历史里服务给的**只有日期**（`YYYY-MM-DD`），一换算就和它对不上。
 *    原始串留在 `exact` 里（界面把它挂在 `title` 上），要查精确值时随时查得到。
 * 2. **排不了时间的照样在**。缺 `created_at` 或读不出的错题进 `undated`，由界面单独报出
 *    几道——与「细则」的排序同一条口径：不许因为排不了序就把它丢了。
 */

/** `YYYY-MM-DD` 与 `YYYY-MM-DDTHH:MM` 都认。认不出就返回 `null`（不猜）。 */
export function parseMoment(value) {
  const text = typeof value === 'string' ? value.trim() : '';
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(text);
  if (!match) {
    return null;
  }
  const [, year, month, day, hour, minute] = match;
  // **范围要验**：`2026-10-04T25:99` 这种串形状对、时刻不存在。不验的话它会被
  // 当成「读得出」，而排序那一侧（`Date.parse`）读不出——同一个事实又长出两套判据。
  const numbers = {
    year: Number(year),
    month: Number(month),
    day: Number(day),
    hour: hour === undefined ? null : Number(hour),
    minute: minute === undefined ? null : Number(minute),
  };
  if (numbers.month < 1 || numbers.month > 12) return null;
  if (numbers.day < 1 || numbers.day > 31) return null;
  if (numbers.hour !== null && (numbers.hour > 23 || numbers.minute > 59)) return null;
  return {
    ...numbers,
    day_key: `${year}-${month}-${day}`,
    month_key: `${year}-${month}`,
  };
}

/** 给人读的时刻。读不出时给一句**明说**，不是一个空位。 */
export function formatMoment(value) {
  const parsed = parseMoment(value);
  if (!parsed) {
    return {text: '（服务没给时刻）', exact: null};
  }
  const date = `${parsed.year}-${String(parsed.month).padStart(2, '0')}-${String(parsed.day).padStart(2, '0')}`;
  if (parsed.hour === null || parsed.minute === null) {
    return {text: date, exact: String(value)};
  }
  const time = `${String(parsed.hour).padStart(2, '0')}:${String(parsed.minute).padStart(2, '0')}`;
  return {text: `${date} ${time}`, exact: String(value)};
}

/** 只到天：重做历史里服务给的就是这个粒度（`YYYY-MM-DD`）。 */
export function formatDay(value) {
  const parsed = parseMoment(value);
  return parsed ? parsed.day_key : '（服务没给时刻）';
}

/** 月份的中文标题：`2026-10` → `2026 年 10 月`。 */
export function monthLabel(key) {
  const parsed = parseMoment(`${key}-01`);
  if (!parsed) {
    return key;
  }
  return `${parsed.year} 年 ${parsed.month} 月`;
}

/** 某一天所在那一周的**周一**（用 UTC 运算，免得本机时区把它挪一天）。 */
export function mondayOf(parsed) {
  const date = new Date(Date.UTC(parsed.year, parsed.month - 1, parsed.day));
  const offset = (date.getUTCDay() + 6) % 7;      // 周一 = 0
  date.setUTCDate(date.getUTCDate() - offset);
  const year = date.getUTCFullYear();
  const month = String(date.getUTCMonth() + 1).padStart(2, '0');
  const day = String(date.getUTCDate()).padStart(2, '0');
  return {year, month, day, day_key: `${year}-${month}-${day}`};
}

/** ISO 周号（周一为一周之始，第 1 周是含 1 月 4 日的那一周）。 */
export function isoWeekNo(parsed) {
  const date = new Date(Date.UTC(parsed.year, parsed.month - 1, parsed.day));
  const day = (date.getUTCDay() + 6) % 7;
  date.setUTCDate(date.getUTCDate() - day + 3);        // 挪到那一周的周四
  const firstThursday = new Date(Date.UTC(date.getUTCFullYear(), 0, 4));
  const firstDay = (firstThursday.getUTCDay() + 6) % 7;
  firstThursday.setUTCDate(firstThursday.getUTCDate() - firstDay + 3);
  return 1 + Math.round((date - firstThursday) / (7 * 24 * 3600 * 1000));
}

function rangeLabel(from, to) {
  const a = parseMoment(from);
  const b = parseMoment(to);
  if (!a || !b) {
    return `${from}–${to}`;
  }
  return `${a.month}月${a.day}日–${b.month}月${b.day}日`;
}

/**
 * 错题按月索引；**最近那一个月再按周**。
 *
 * 返回 `{months, undated}`：
 *   `months[i] = {key, label, count, ids, weeks?}`——`weeks` 只有最近一个月有，
 *   每项 `{key(周一那天), label('10月5日–10月11日'), week_no, count, ids}`。
 * 月份与周都**由新到老**排（与「细则」一致）。
 */
export function timeIndex(problems) {
  const byMonth = new Map();
  const undated = [];

  for (const problem of problems || []) {
    const parsed = parseMoment(problem?.created_at);
    if (!parsed) {
      undated.push(problem);
      continue;
    }
    if (!byMonth.has(parsed.month_key)) {
      byMonth.set(parsed.month_key, {key: parsed.month_key, problems: []});
    }
    byMonth.get(parsed.month_key).problems.push({problem, parsed});
  }

  // ⚠ **不许写成 `[...byMonth.values()]`**：打包器在 loose 模式下把展开编译成
  // `[].concat(n.values())`，而 `concat` **不展开迭代器**——它会把 MapIterator 本身
  // 当成一个元素装进数组。症状很阴：`node --test` 全绿（Node 不做那种变换），
  // 浏览器里却在 `.map((bucket) => bucket.problems…)` 上崩（`bucket` 是迭代器）。
  // `Array.from` 是普通调用，任何模式下都对。`site/tests/bundle-safe.test.mjs` 会拦这条。
  const months = Array.from(byMonth.values())
    .sort((a, b) => b.key.localeCompare(a.key))
    .map((bucket) => {
      const rows = bucket.problems.slice().sort((a, b) => b.parsed.day_key.localeCompare(a.parsed.day_key));
      return {
        key: bucket.key,
        label: monthLabel(bucket.key),
        count: rows.length,
        ids: rows.map((row) => row.problem.id),
        rows,
      };
    });

  if (months.length > 0) {
    const newest = months[0];
    const byWeek = new Map();
    for (const row of newest.rows) {
      const monday = mondayOf(row.parsed);
      if (!byWeek.has(monday.day_key)) {
        byWeek.set(monday.day_key, {key: monday.day_key, monday, problems: []});
      }
      byWeek.get(monday.day_key).problems.push(row.problem);
    }
    newest.weeks = Array.from(byWeek.values())
      .sort((a, b) => b.key.localeCompare(a.key))
      .map((bucket) => {
        const end = new Date(Date.UTC(bucket.monday.year, Number(bucket.monday.month) - 1,
                                      Number(bucket.monday.day) + 6));
        const to = `${end.getUTCFullYear()}-${String(end.getUTCMonth() + 1).padStart(2, '0')}-` +
                   `${String(end.getUTCDate()).padStart(2, '0')}`;
        return {
          key: bucket.key,
          label: rangeLabel(bucket.key, to),
          week_no: isoWeekNo(bucket.monday),
          count: bucket.problems.length,
          ids: bucket.problems.map((problem) => problem.id),
        };
      });
  }

  return {months, undated};
}

/**
 * 按 `{month, week}` 过滤。认不出的键**明确失败**——悄悄退成「全部」会让一次
 * 失效的链接看起来像一次正常跳转（与地址解析那条口径一致）。
 *
 * **周必须真的属于那个月**：以前这里在月份桶里找不到周时会去别的月份里捞，
 * 于是 `?month=2026-09&week=2026-10-05` 会安安静静地显示 10 月那一周——
 * 一个指向别处的链接看起来像正常跳转，正是要防的那类。
 */
export function filterByTime(problems, {month = null, week = null} = {}) {
  if (!month && !week) {
    return {problems: problems || [], label: null};
  }
  const index = timeIndex(problems);
  const monthBucket = index.months.find((m) => m.key === month);
  if (!monthBucket) {
    return {
      error: {
        code: 'month_unknown',
        message: `索引里没有这个月：${month}`,
        hint: '月份写成 `YYYY-MM`（例如 2026-10）；「显示全部」可以清掉这个筛选。',
      },
    };
  }

  let bucket = monthBucket;
  if (week) {
    const weeks = monthBucket.weeks || [];
    bucket = weeks.find((w) => w.key === week) || null;
    if (!bucket) {
      return {
        error: {
          code: 'week_unknown',
          message: `${monthBucket.label}里没有这一周：${week}`,
          hint: weeks.length > 0
            ? `这个月索引到的周（那一周的周一，YYYY-MM-DD）：` +
              `${weeks.map((w) => w.key).join('、')}。`
            : '只有**最近那一个月**才按周索引；更早的月份只到月。',
        },
      };
    }
  }

  const ids = new Set(bucket.ids);
  return {
    problems: (problems || []).filter((problem) => ids.has(problem.id)),
    label: bucket.label,
    key: bucket.key,
  };
}
