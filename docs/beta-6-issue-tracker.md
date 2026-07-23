# ASR Beta 6 Issue Tracker

## Public Four-Digit Node Selection

AllStarLink still has legitimate public four-digit nodes beginning at `2000`.
Only `1000` through `1999` are reserved for private/local nodes.

Required behavior:

- Clicking a public node number in Connection Status populates the Node # field in Node Controls.
- Clicking a public node number in Favorites populates the same field and retains the existing menu-close behavior.
- Private/local nodes `1000` through `1999` do not populate Node Controls.
- Lookup does not hide a legitimate public four-digit node merely because it has four digits.
- Configured private bridge nodes remain excluded from public lookup targets.

The source fix is included in Beta 6 development and shipped in the Beta 5.11
prerelease. A no-version-bump Beta 5.10 patch was used first to validate the
two click paths on KE7WIL, Thomas/KN4EWT, and Del Webb/NY7S.
