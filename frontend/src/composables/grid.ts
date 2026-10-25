/** Presentation only: grid index j of a series with step n -> seconds on the relative time axis.
 * The alignment itself is always on the integer index k = j * n (RI-25); floats appear only here. */
export function gridSeconds(jStart: number, n: number, count: number, stepBaseUs: number, shiftK = 0): Float64Array {
  const x = new Float64Array(count)
  // the index (j·n + shift) is an exact integer; only the final conversion to seconds is floating point
  for (let i = 0; i < count; i++) x[i] = (((jStart + i) * n + shiftK) * stepBaseUs) / 1e6
  return x
}
