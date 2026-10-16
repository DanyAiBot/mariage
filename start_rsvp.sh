#!/bin/bash
# Démarre le service RSVP/admin (port 8765). Le tunnel public pointe dessus.
cd /opt/data/workspace/mariage
echo "clé admin : $(cat .admin_key)"
exec python3 rsvp_server.py
