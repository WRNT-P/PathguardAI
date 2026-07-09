# pathguard/backend/app/mock/__init__.py
"""Synthetic / mock data generators for development and testing.

Nothing on the runtime serving path may import from this package. It exists to
feed offline evaluation, the test suite, and DB seeding with SIMULATED data.
"""
