# Demo audit

Amounts in the Base and Shares columns are CAD.

Seq | Date | Description | Raw | Payer | Amount | Explicit | Resolved | Source | Allocation | Base | Shares | Status
--- | --- | --- | --- | --- | ---: | --- | --- | --- | --- | ---: | --- | ---
1 | 2026-08-15 | Breakfast for Alice and Bob | L60 CAD | alice | 60 | CAD | CAD | explicit | equal_split: alice, bob | 60 | alice=30, bob=30 | VALID
2 | 2026-08-15 | Tickets for everyone | C90A | charlie | 90 | — | CAD | inherited | all_equal: alice, bob, charlie | 90 | alice=30, bob=30, charlie=30 | VALID
3 | 2026-08-16 | Bob fronts Charlie's purchase | B24C USD | bob | 24 | USD | USD | explicit | single: charlie | 30 | charlie=30 | VALID
4 | 2026-08-14 | Earlier date entered later: shared taxi | L48A | alice | 48 | — | USD | inherited | all_equal: alice, bob, charlie | 60 | alice=20, bob=20, charlie=20 | VALID
5 | 2026-08-16 | Charlie's payment for Bob; currency corrected separately | C20B EUR | charlie | 20 | EUR | CAD | override | single: bob | 20 | bob=20 | OVERRIDDEN
6 | 2026-08-16 | Shared parking inherits corrected currency | B30A | bob | 30 | — | CAD | inherited | all_equal: alice, bob, charlie | 30 | alice=10, bob=10, charlie=10 | VALID

## Final output

Status: SETTLEMENT_READY
Base currency: CAD

Balances
Participant | Paid | Share | Net | Action
--- | ---: | ---: | ---: | ---
alice | 120.00 | 90.00 | 30.00 | receive
bob | 60.00 | 110.00 | -50.00 | pay
charlie | 110.00 | 90.00 | 20.00 | receive

Settlements
bob -> alice: 30.00 CAD
bob -> charlie: 20.00 CAD

Currency segments
#1-#2: CAD
#3-#4: USD
#5-#6: CAD

## Checks

- Golden rows, balances, segments and transfers: PASS
- Paid = Share = 290 CAD; Net = 0: PASS
- All balances are zero after transfers: PASS
- Raw text and input order preserved: PASS
- Without correction: total 315 CAD; nets Alice +25, Bob -50, Charlie +25.
- Unknown initial currency: AUDIT_REQUIRED, zero settlement transfers.
