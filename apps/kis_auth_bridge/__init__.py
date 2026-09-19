# Deliberately not registered in INSTALLED_APPS. This package has no
# models, migrations, signals, or admin registrations — it's a plain
# Python package containing views + a URL include, which Django resolves
# by dotted import path regardless of the app registry. Registering it
# would require editing config/settings/base.py, which had unrelated
# uncommitted changes in flight from a concurrent session at the time
# this was written; staying out of the app registry avoids that edit
# entirely. Revisit only if this app ever needs models/migrations.
