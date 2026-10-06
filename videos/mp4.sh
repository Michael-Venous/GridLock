#!/bin/bash

# Directory to store original files
ORIGINALS_DIR="./originals"

# Create the originals directory if it doesn't exist
mkdir -p "$ORIGINALS_DIR"

# Loop through all .mp4 and .MP4 files in the current directory
for file in *.mp4 *.MP4; do
    # Skip if no files are found
    if [ ! -f "$file" ]; then
        continue
    fi

    # Temporary output file
    temp_output="${file}.temp.mp4"

    # Convert audio stream to FLAC while keeping the video stream
    ffmpeg -i "$file" -c:v copy -c:a flac "$temp_output"

    # Move the original file to the originals directory
    mv "$file" "$ORIGINALS_DIR/"

    # Rename the temporary output file to the original filename
    mv "$temp_output" "$file"

    echo "Converted $file and moved the original to $ORIGINALS_DIR/"
done

echo "All files have been processed."
