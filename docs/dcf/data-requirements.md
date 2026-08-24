# DCF data requirements

| Domain | Critical normalized fields | Lineage retained |
| --- | --- | --- |
| Financial statement | revenue, EBIT, EBITDA/D&A basis, capex, current assets/liabilities, cash, debt | period, availability time, source URL, document hash/version where available |
| Security | diluted shares, currency, issuer sector/classification | instrument and issuer IDs, source |
| Market | current factual price | observation time, source, URL, data mode, freshness warning |
| Macro | five-year risk-free rate, long-term inflation/growth basis | effective date, fetched time, source and record IDs |
| Policy | tax, ERP, beta/debt fallbacks, terminal-growth cap | configuration and assumption version |

Corporate FY statements are preferred for the MVP. Missing critical data blocks valuation. Unit and currency normalization must happen before the input builder; cross-currency valuation is unsupported until a versioned FX snapshot is supplied. New statements, restatements, share issues, debt changes, macro versions or model versions change the snapshot and require a new valuation.
