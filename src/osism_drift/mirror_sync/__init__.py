"""Mirror sync: compute a Plan for a per-release defaults re-sync.

Detection lives in osism_drift.drift; this package is the generation side. One
pure Plan is computed and then either rendered (report) or written (--apply), so
the two can never describe different trees.
"""
