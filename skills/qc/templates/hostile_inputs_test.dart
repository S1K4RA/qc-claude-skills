// QC hostile-input tests (Dart / Flutter). Temporary: copy into test/qc_tmp/,
// fill in the TODOs, run `dart test test/qc_tmp` (Flutter: swap the import for
// package:flutter_test/flutter_test.dart and run `flutter test test/qc_tmp`),
// report, then delete test/qc_tmp/.
import 'dart:math';

import 'package:test/test.dart';
// TODO: import the unit under test, e.g. import 'package:my_app/models/order.dart';

// TODO: call the unit under test. It should return or throw a handled error.
Object? parse(Map<String, dynamic> json) =>
    throw UnimplementedError('TODO: e.g. Order.fromJson(json)');

// TODO: the error types the code is *meant* to throw on bad input. Anything else
// (TypeError from an `as` cast, NoSuchMethodError, RangeError...) is a finding.
bool isHandled(Object e) => e is FormatException;

// TODO: one valid input, and the keys that must be present.
final Map<String, dynamic> validSample = {
  'id': 42,
  'name': 'Widget',
  'price': 9.99,
  'createdAt': '2026-01-31T10:00:00Z',
  'tags': ['a', 'b'],
};
const requiredKeys = {'id', 'name'};

const seed = 1337; // fuzz findings must quote this
const fuzzIterations = 500;

const _twoPow53 = 9007199254740992;
const numberEdges = <num>[0, -1, 1 << 31, -(1 << 31), _twoPow53, -_twoPow53,
    0.1, -0.0, double.nan, double.infinity, double.negativeInfinity,
    double.maxFinite, double.minPositive];
final stringEdges = <String>['', ' ', '\t\n', 'a' * 100000, '😀👍🏽👨‍👩‍👧',
    'Ω≈ç√∫', 'a\u0000b', '\u202Eevil', 'null', 'NaN', '-1',
    '2026-02-30', '0000-00-00', '9999-12-31T23:59:60Z', 'not-a-date',
    "' OR '1'='1", '<script>alert(1)</script>', '../../etc/passwd', r'${7*7}'];
final listEdges = <Object?>[[], [null], List.filled(10000, 'x'), [[], {}]];
final junk = <Object?>[null, true, 0, '', [], {}, {'nested': {'deeper': [1, 'x', null]}}];

/// Edge values of the same kind as [sample], so edge stays apart from negative.
List<Object?> edgesFor(Object? sample) => switch (sample) {
      num() => numberEdges,
      String() => stringEdges,
      List() => listEdges,
      _ => junk,
    };

Object? randomValue(Random r, [int depth = 0]) {
  final all = [...numberEdges, ...stringEdges, ...junk];
  switch (r.nextInt(depth > 2 ? 3 : 5)) {
    case 0:
      return all[r.nextInt(all.length)];
    case 1:
      return String.fromCharCodes(
          List.generate(r.nextInt(40), (_) => r.nextInt(0xFFFF)));
    case 2:
      return (r.nextDouble() - 0.5) * pow(10, r.nextInt(20));
    case 3:
      return List.generate(r.nextInt(4), (_) => randomValue(r, depth + 1));
    default:
      final n = r.nextInt(4);
      return {for (var i = 0; i < n; i++) 'k$i': randomValue(r, depth + 1)};
  }
}

/// A copy of [validSample] with keys dropped, swapped for junk, or added.
Map<String, dynamic> mutate(Random r) {
  final m = <String, dynamic>{};
  validSample.forEach((k, v) {
    final roll = r.nextInt(10);
    if (roll == 0) return; // drop
    m[k] = roll < 5 ? randomValue(r) : v;
  });
  if (r.nextInt(5) == 0) m['unexpected'] = randomValue(r); // extra key
  return m;
}

void expectParsesOrHandled(Map<String, dynamic> input, String label) {
  try {
    parse(input);
  } catch (e, st) {
    if (!isHandled(e)) fail('$label: unhandled ${e.runtimeType}: $e\n$st');
  }
}

String show(Object? v) {
  final s = '$v';
  return s.length > 60 ? '${s.substring(0, 60)}... (${s.length} chars)' : s;
}

void main() {
  test('valid sample parses', () => expect(() => parse(validSample), returnsNormally));

  group('negative', () {
    for (final key in validSample.keys) {
      final without = Map.of(validSample)..remove(key);
      if (requiredKeys.contains(key)) {
        test('missing required $key is rejected', () {
          expect(() => parse(without), throwsA(predicate<Object>(isHandled)));
        });
      } else {
        test('missing $key', () => expectParsesOrHandled(without, 'missing $key'));
      }
      for (final bad in junk.where((j) => j.runtimeType != validSample[key].runtimeType)) {
        test('$key = ${show(bad)} (wrong type)', () {
          expectParsesOrHandled({...validSample, key: bad}, '$key=${show(bad)}');
        });
      }
    }
  });

  group('edge', () {
    validSample.forEach((key, sample) {
      for (final v in edgesFor(sample)) {
        test('$key = ${show(v)}', () => expectParsesOrHandled({...validSample, key: v}, '$key=${show(v)}'));
      }
    });
  });

  group('fuzz', () {
    test('$fuzzIterations seeded random inputs (seed $seed)', () {
      final r = Random(seed);
      for (var i = 0; i < fuzzIterations; i++) {
        final input = mutate(r);
        expectParsesOrHandled(input, 'seed=$seed iteration=$i input=${show(input)}');
      }
    }, timeout: const Timeout(Duration(seconds: 60))); // a hang is a finding too
  });
}
