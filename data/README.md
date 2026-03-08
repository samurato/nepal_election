This directory is managed automatically by the GitHub Actions workflow
`.github/workflows/fetch-election-data.yml`.

The workflow runs every 10 minutes, fetches the latest Nepal 2082 election
results from result.election.gov.np, merges everything into a single JSON file,
and commits it here.  GitHub Pages serves this directory so the frontend can
read the data directly without going through a proxy.

File
----
data.json   — a single merged file containing:
              parties[]    party seat totals (FPTP won + leading, symbolId, prVotes)
              pr_parties[] full party-list vote totals (party, symbolId, prVotes)
              winners[]    per-constituency elected candidates (with candidateId, symbolId)
              candidates[] all candidates from the central dataset (for drill-down)

GitHub Pages URL
----------------
https://<YOUR_GITHUB_USERNAME>.github.io/<YOUR_REPO>/data/data.json
