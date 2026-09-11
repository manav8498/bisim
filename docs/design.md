# How bisim computes an address

This page explains the address format, what goes into it, and the rules that keep it stable. Read it before changing anything in `bisim/probes.py`, `bisim/canon.py`, `bisim/_runner.py`, or `bisim/address.py`.

## The address

```
address = "bsm1:" + sha256("bisim/1" | probegen | sig_hash | root)
```

- `probegen` is the version of the input generator, currently `v1`.
- `sig_hash` is a hash of the parameter types and the return type. Parameter names are not included, so renaming a parameter does not change the address. For a class, it covers the constructor types and the name, kind, parameter types and return type of every public method.
- `root` is the root of a Merkle tree over the (input, result) pairs. The leaves are `sha256(input_id + result_hash)`, sorted, so the order in which inputs were run does not matter.

## Inputs

There are three kinds of inputs, called probes.

- **type**: generated from the type hints by a seeded generator. Edge cases come first, then seeded random values. The seed comes from `sig_hash`, so the same signature gives the same inputs everywhere.
- **witness**: an input a person approved, with the expected result. Stored in `.bisim/witness/`.
- **suggested**: an input a model proposed (from `mint` or `reach`) that reached code the generated inputs missed. Stored in the same file, but not approved by anyone.

A probe's id is the hash of its arguments in canonical form. If the same input appears in more than one kind, the strongest kind wins: witness, then suggested, then type.

For a method target the arguments are `(constructor_args, ((method, args),))`. For a class target they are `(constructor_args, ((method1, args1), (method2, args2), ...))` with one to four calls.

## Results

Each input is run in a child process. The result record is one of:

- `{"ok": true, "value": ...}` for a normal return
- `{"ok": false, "exc": "ValueError"}` for an exception (the type only, not the message)
- `{"timeout": true}` when the call exceeded the time limit

Optional fields:

- `out`: anything the call printed
- `effects`: files opened, files left in the scratch directory (with content hashes), environment variables read, and blocked network or subprocess attempts. Present only when the list is not empty.
- for class and method targets: `steps` (one record per call) and `state` (the public attributes of the object afterwards). A failing constructor gives `{"ok": false, "exc": ..., "at": "init"}`.

Every input is run twice, in two separate processes. If the two results differ, the target is refused and no address is produced.

## Canonical form

Values are converted to a small tagged tree before hashing so that the same value hashes the same way everywhere:

| value | form |
|---|---|
| None | `["n"]` |
| bool | `["B", 0 or 1]` |
| int | `["i", "decimal string"]` |
| float | `["f", repr]` with `nan`, `inf`, `-inf` and `-0.0` handled explicitly |
| str | `["s", text]` |
| bytes | `["b", hex]` |
| list, tuple | `["l", [...]]`, `["t", [...]]` |
| set, frozenset | `["S", sorted items]` |
| dict | `["d", pairs sorted by key]` |
| dataclass | `["D", class name, fields sorted by name]` |
| other object | `["o", class name, public attributes sorted by name]` |
| anything else | `["r", type name, repr]`, marked opaque |

Opaque values are allowed but flagged, because `repr` output may differ between machines.

## Coverage

While running the inputs, bisim records which lines of the target executed and which line-to-line transitions happened. From that it reports:

- line coverage: executable lines of the target that ran at least once
- branch coverage: for each `if`, `elif`, `while` and `for`, whether the true branch and the false branch were each taken at least once

Coverage is reported alongside the address. It is not part of the hash.

`diff` and `check` double the number of generated inputs (48, 96, 192, up to 480) until every line and branch has run or coverage stops improving.

## Rules for changing the code

1. Any change to how inputs are generated must change `PROBEGEN_VERSION`. Old addresses must not silently collide with new ones.
2. Any change to result records or canonical form must keep the fixture tests green: every refactor pair keeps its address, every mutant pair changes it.
3. Adding a new field to a result record must not change the result for code that does not use the feature. This is why `effects` is only present when non-empty.
4. Never produce an address for a target that could not be run deterministically. Refuse and say why.
5. Every "changed" verdict must come with a concrete input and both results.

## Version history of the format

- 1.0: functions, methods and classes; effects; line and branch coverage; structural form for objects. The address format has not changed since the first release, so addresses computed by any 1.x version are comparable.
