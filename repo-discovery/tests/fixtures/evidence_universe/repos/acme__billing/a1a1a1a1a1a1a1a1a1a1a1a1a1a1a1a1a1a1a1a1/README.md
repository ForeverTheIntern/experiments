<p align="center"><img src="docs/logo.png" alt="Acme Billing"></p>

# Acme Billing

[![Build Status](https://ci.acme.dev/billing/badge.svg)](https://ci.acme.dev/billing)
[![npm](https://img.shields.io/npm/v/@acme/billing.svg)](https://www.npmjs.com/package/@acme/billing)

Acme Billing turns subscriptions into **monthly invoices**. It computes line items,
applies tax with a configurable rounding mode, and issues refunds against settled
charges.

## Installation

```bash
npm install @acme/billing
```

## Tax rounding

Every charge records the rounding mode used for its tax, so later adjustments can
reuse it. See [the rounding guide](docs/rounding.md).

## License

MIT
