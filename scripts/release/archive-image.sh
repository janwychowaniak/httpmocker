#!/usr/bin/env bash
# Save a published image into the archive attached to its release, and prove that the
# archive loads, into a store that no longer holds the image, as the same image.
#
#   archive-image.sh <image:version> <expected image id> <archive path>
#
# The image must be present under <image:version>. It is saved by that tag, never by its
# Id: an archive saved by Id loads with no tag at all. The Id is what a receiving host
# compares after `docker load`; it survives the save/load round trip, while the registry
# digest does not.
set -euo pipefail

image=$1
expected=$2
archive=$3

actual=$(docker image inspect --format '{{.Id}}' "$image")
if [[ "$actual" != "$expected" ]]; then
    echo "::error::$image is $actual, but the image that was built is $expected"
    exit 1
fi

docker save "$image" | gzip -n > "$archive"

# The round trip. A load over an image that is still present changes nothing and reads
# as success, so the removal is checked rather than assumed.
docker rmi --force "$expected" > /dev/null
if docker image inspect "$expected" > /dev/null 2>&1; then
    echo "::error::$expected is still present after its removal, so a load would prove nothing"
    exit 1
fi
docker load --input "$archive"
loaded=$(docker image inspect --format '{{.Id}}' "$image")
if [[ "$loaded" != "$expected" ]]; then
    echo "::error::$archive loads as $loaded, not as $expected"
    exit 1
fi
echo "$archive holds $image, Id $loaded"
