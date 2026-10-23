const productHuntDateFormat = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/Los_Angeles',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

function validCalendarDay(day: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return false;
  const year = Number(day.slice(0, 4));
  if (year === 0) return false;
  const month = Number(day.slice(5, 7));
  const date = Number(day.slice(8, 10));
  const utc = new Date(0);
  utc.setUTCFullYear(year, month - 1, date);
  return utc.getUTCFullYear() === year && utc.getUTCMonth() === month - 1 && utc.getUTCDate() === date;
}

export function productHuntToday(now: Date = new Date()): string {
  const parts = productHuntDateFormat.formatToParts(now);
  const year = parts.find((part) => part.type === 'year')?.value;
  const month = parts.find((part) => part.type === 'month')?.value;
  const day = parts.find((part) => part.type === 'day')?.value;
  return `${year}-${month}-${day}`;
}

export function shiftProductHuntDate(day: string, days: number): string {
  if (!validCalendarDay(day) || !Number.isInteger(days)) throw new RangeError('Invalid Product Hunt date or offset');
  const utc = new Date(0);
  utc.setUTCFullYear(Number(day.slice(0, 4)), Number(day.slice(5, 7)) - 1, Number(day.slice(8, 10)));
  utc.setUTCDate(utc.getUTCDate() + days);
  const shifted = utc.toISOString().slice(0, 10);
  if (!validCalendarDay(shifted)) throw new RangeError('Product Hunt date is out of range');
  return shifted;
}

export function isProductHuntDate(day: string, today: string): boolean {
  return validCalendarDay(day) && validCalendarDay(today) && day <= today;
}

/** Month math uses UTC so the grid never changes with the device timezone or DST. */
export function productHuntMonthGrid(month: string): { leadingDays: number; daysInMonth: number } {
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month) || month.slice(0, 4) === '0000') {
    throw new RangeError('Invalid Product Hunt month');
  }
  const year = Number(month.slice(0, 4));
  const monthIndex = Number(month.slice(5, 7)) - 1;
  const first = new Date(0);
  first.setUTCFullYear(year, monthIndex, 1);
  const last = new Date(0);
  last.setUTCFullYear(year, monthIndex + 1, 0);
  return { leadingDays: first.getUTCDay(), daysInMonth: last.getUTCDate() };
}

export function shiftProductHuntMonth(month: string, offset: number): string {
  productHuntMonthGrid(month);
  if (!Number.isInteger(offset)) throw new RangeError('Invalid Product Hunt month offset');
  const date = new Date(0);
  date.setUTCFullYear(Number(month.slice(0, 4)), Number(month.slice(5, 7)) - 1 + offset, 1);
  const year = date.getUTCFullYear();
  if (year < 1 || year > 9999) throw new RangeError('Product Hunt month is out of range');
  return `${String(year).padStart(4, '0')}-${String(date.getUTCMonth() + 1).padStart(2, '0')}`;
}
