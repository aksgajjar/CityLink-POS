#!/bin/bash
# Double-click this file in Finder to update and launch CityLink POS.
#
# macOS opens .command files in Terminal and runs them. We resolve the
# real project root from this file's location, then hand off to
# scripts/update.sh.

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${DIR}/scripts/update.sh"

if [ ! -f "${SCRIPT}" ]; then
  echo "Update script not found at: ${SCRIPT}"
  echo "Make sure you opened this file from inside the CityLink-POS folder."
  read -n 1 -s -r -p "Press any key to close this window…"
  exit 1
fi

# Make sure update.sh is executable (in case Finder/git stripped the bit).
chmod +x "${SCRIPT}"

bash "${SCRIPT}"
EXIT_CODE=$?

# If the app exited with an error, keep terminal open so tester can read it.
if [ "${EXIT_CODE}" -ne 0 ]; then
  echo
  echo "App exited with code ${EXIT_CODE}."
  echo "See errors.log in the project folder for details."
  read -n 1 -s -r -p "Press any key to close this window…"
fi
