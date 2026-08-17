# Licensed KASE history import

The production forecast release requires real historical equity observations.
KASE classifies archived trading information as a commercial product, so the
application never downloads or purchases it automatically.

After the operator has lawfully obtained a **deals-register CSV** from KASE,
preview it without changing the database:

```powershell
python scripts/kase.py import-kase-history `
  --path C:\licensed-data\DEALS.csv `
  --license-acknowledged
```

The command requires the acknowledgement flag and defaults to dry-run. Review
the emitted counts, then explicitly write the validated bars:

```powershell
python scripts/kase.py import-kase-history `
  --path C:\licensed-data\DEALS.csv `
  --license-acknowledged `
  --commit
```

The importer performs no network calls. It accepts the official CSV deal
register fields (`Date`, `Time`, `Inst_Type`, `Symbol`, `Price`, `Volume`, and
optional trading metadata), keeps equity market trades from the regular market,
excludes negotiated/special trades, and aggregates each ticker/session into
OHLCV, turnover and trade count. Unknown tickers are reported, source-file and
row hashes make imports auditable and idempotent, and dry-run is isolated in a
database savepoint.

CSV is supported because the official archive specification names CSV and XLS
as delivery formats. Convert an XLS delivery to CSV locally without changing
column names before running this command.
