// QC hostile-input tests (TypeScript, Vitest). Temporary: copy into a qc_tmp/
// folder next to the existing tests, fill in the TODOs, run
// `npx vitest run qc_tmp`, report, then delete qc_tmp/.
// Jest: delete the vitest import below (Jest provides these as globals).
import { describe, it, expect } from 'vitest';
// TODO: import the unit under test, e.g. import { parseOrder } from '../../src/orders';

type Json = Record<string, unknown>;

// TODO: call the unit under test. It should return or throw a handled error.
const parse = (input: Json): unknown => {
  throw new Error(`TODO: e.g. parseOrder(input) for ${Object.keys(input)}`);
};

// TODO: the error types the code is *meant* to throw on bad input, e.g.
// `e instanceof ZodError || e instanceof ValidationError`. Anything else
// (TypeError "cannot read properties of undefined", RangeError...) is a finding.
const isHandled = (e: unknown): boolean => e instanceof SyntaxError;

// TODO: one valid input, and the keys that must be present.
const validSample: Json = {
  id: 42,
  name: 'Widget',
  price: 9.99,
  createdAt: '2026-01-31T10:00:00Z',
  tags: ['a', 'b'],
};
const requiredKeys = new Set(['id', 'name']);

const SEED = 1337; // fuzz findings must quote this
const FUZZ_ITERATIONS = 500;

const numberEdges = [0, -0, -1, 2 ** 31, -(2 ** 31), 2 ** 53, -(2 ** 53), 2 ** 53 + 2,
  0.1, NaN, Infinity, -Infinity, Number.MAX_VALUE, Number.MIN_VALUE];
const stringEdges = ['', ' ', '\t\n', 'a'.repeat(100_000), '😀👍🏽👨‍👩‍👧', 'Ω≈ç√∫',
  'a\u0000b', '\u202Eevil', 'null', 'undefined', 'NaN', '-1',
  '2026-02-30', '0000-00-00', '9999-12-31T23:59:60Z', 'not-a-date',
  "' OR '1'='1", '<script>alert(1)</script>', '../../etc/passwd', '${7*7}'];
const listEdges = [[], [null], Array(10_000).fill('x'), [[], {}]];
const junk: unknown[] = [null, undefined, true, 0, '', [], {}, { nested: { deeper: [1, 'x', null] } },
  JSON.parse('{"__proto__": {"polluted": true}}')];

/** Edge values of the same kind as the sample, so edge stays apart from negative. */
const edgesFor = (sample: unknown): unknown[] =>
  typeof sample === 'number' ? numberEdges
    : typeof sample === 'string' ? stringEdges
      : Array.isArray(sample) ? listEdges : junk;

/** mulberry32: small seeded PRNG, so every fuzz run reproduces. */
const rng = (seed: number) => () => {
  seed = (seed + 0x6d2b79f5) | 0;
  let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};
const int = (r: () => number, n: number) => Math.floor(r() * n);

const randomValue = (r: () => number, depth = 0): unknown => {
  const all = [...numberEdges, ...stringEdges, ...junk];
  switch (int(r, depth > 2 ? 3 : 5)) {
    case 0: return all[int(r, all.length)];
    case 1: return String.fromCharCode(...Array.from({ length: int(r, 40) }, () => int(r, 0xffff)));
    case 2: return (r() - 0.5) * 10 ** int(r, 20);
    case 3: return Array.from({ length: int(r, 4) }, () => randomValue(r, depth + 1));
    default: return Object.fromEntries(Array.from({ length: int(r, 4) }, (_, i) => [`k${i}`, randomValue(r, depth + 1)]));
  }
};

/** A copy of validSample with keys dropped, swapped for junk, or added. */
const mutate = (r: () => number): Json => {
  const m: Json = {};
  for (const [k, v] of Object.entries(validSample)) {
    const roll = int(r, 10);
    if (roll === 0) continue; // drop
    m[k] = roll < 5 ? randomValue(r) : v;
  }
  if (int(r, 5) === 0) m.unexpected = randomValue(r); // extra key
  return m;
};

const expectParsesOrHandled = (input: Json, label: string) => {
  try {
    parse(input);
  } catch (e) {
    if (!isHandled(e)) throw new Error(`${label}: unhandled ${(e as Error)?.name}: ${(e as Error)?.message}`, { cause: e });
  }
};

const show = (v: unknown): string => {
  const s = typeof v === 'number' || v === undefined ? String(v) : JSON.stringify(v);
  return s.length > 60 ? `${s.slice(0, 60)}... (${s.length} chars)` : s;
};

it('valid sample parses', () => expect(() => parse(validSample)).not.toThrow());

describe('negative', () => {
  for (const key of Object.keys(validSample)) {
    const { [key]: _omitted, ...without } = validSample;
    if (requiredKeys.has(key)) {
      it(`missing required ${key} is rejected`, () => {
        let error: unknown;
        try { parse(without); } catch (e) { error = e; }
        expect(isHandled(error), `threw ${String(error)}`).toBe(true);
      });
    } else {
      it(`missing ${key}`, () => expectParsesOrHandled(without, `missing ${key}`));
    }
    for (const bad of junk.filter((j) => typeof j !== typeof validSample[key] || Array.isArray(j) !== Array.isArray(validSample[key]))) {
      it(`${key} = ${show(bad)} (wrong type)`, () => expectParsesOrHandled({ ...validSample, [key]: bad }, `${key}=${show(bad)}`));
    }
  }
});

describe('edge', () => {
  for (const [key, sample] of Object.entries(validSample)) {
    for (const v of edgesFor(sample)) {
      it(`${key} = ${show(v)}`, () => expectParsesOrHandled({ ...validSample, [key]: v }, `${key}=${show(v)}`));
    }
  }
});

describe('fuzz', () => {
  it(`${FUZZ_ITERATIONS} seeded random inputs (seed ${SEED})`, () => {
    const r = rng(SEED);
    for (let i = 0; i < FUZZ_ITERATIONS; i++) {
      const input = mutate(r);
      expectParsesOrHandled(input, `seed=${SEED} iteration=${i} input=${show(input)}`);
    }
  }, 60_000); // a hang is a finding too
});
