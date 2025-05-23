#!/bin/bash
set -e
set -o pipefail

if [ "$1" != "test" ] && [ "$1" != "prod" ]; then
  echo "Usage: $0 [test|prod]"
  exit 1
fi

echo "🔍 Running safety scan..."
if ! safety scan; then
  echo "❌ Vulnerabilities found. Aborting upload."
  exit 1
fi

echo "🧹 Cleaning previous builds..."
rm -rf build dist *.egg-info

echo "📦 Building distribution..."
python setup.py sdist bdist_wheel

if [ "$1" == "test" ]; then
  echo "🚀 Uploading to TestPyPI..."
  twine upload --repository testpypi dist/* --verbose
  echo "✅ Uploaded to TestPyPI."
else
  echo "🚀 Uploading to PyPI..."
  twine upload --repository pypi dist/* --verbose
  echo "✅ Uploaded to PyPI."
fi
