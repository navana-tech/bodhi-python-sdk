#!/bin/bash
set -e
set -o pipefail

if [ "$1" != "test" ] && [ "$1" != "prod" ]; then
  echo "Usage: $0 [test|prod]"
  exit 1
fi

if ! python -c "import build" 2>/dev/null; then
  echo "❌ The 'build' package is required: pip install -U build twine"
  exit 1
fi

# safety needs an account these days, so scan when it's available rather than
# blocking the release on it being installed.
if command -v safety >/dev/null 2>&1; then
  echo "🔍 Running safety scan..."
  if ! safety scan; then
    echo "❌ Vulnerabilities found. Aborting upload."
    exit 1
  fi
else
  echo "⚠️  safety not installed, skipping the vulnerability scan"
fi

echo "🧹 Cleaning previous builds..."
rm -rf build dist *.egg-info

# Build with `python -m build`, not `setup.py sdist`. PyPI enforces PEP 625:
# the sdist must be named bodhi_api_sdk-<version>.tar.gz. Older setuptools emits
# bodhi-api-sdk-<version>.tar.gz, which PyPI rejects with a 400 *after* the wheel
# has already uploaded — burning the version number.
echo "📦 Building distribution..."
python -m build

echo "🔍 Checking metadata and filenames..."
twine check dist/*
for f in dist/*.tar.gz; do
  case "$(basename "$f")" in
    bodhi_api_sdk-*) ;;
    *) echo "❌ $f is not PEP 625 normalised; upgrade setuptools"; exit 1 ;;
  esac
done

if [ "$1" == "test" ]; then
  echo "🚀 Uploading to TestPyPI..."
  twine upload --repository testpypi dist/* --verbose
  echo "✅ Uploaded to TestPyPI."
else
  echo "🚀 Uploading to PyPI..."
  twine upload --repository pypi dist/* --verbose
  echo "✅ Uploaded to PyPI."
fi
