#!/bin/bash

# 1. Get the current remote URL
CURRENT_URL=$(git remote get-url origin 2>/dev/null)

if [ -z "$CURRENT_URL" ]; then
    echo "Error: No remote 'origin' found in this directory."
    exit 1
fi

# 2. Check if it's already HTTPS
if [[ "$CURRENT_URL" == https://* ]]; then
    echo "Remote is already using HTTPS: $CURRENT_URL"
else
    # 3. Convert SSH format to HTTPS format
    # Example: git@github.com:gadyma/SSLCheck.git -> https://github.com/gadyma/SSLCheck.git
    NEW_URL=$(echo "$CURRENT_URL" | sed -E 's|git@github.com:|https://github.com/|' | sed 's|.git$|.git|')
    
    echo "Changing origin from SSH to HTTPS..."
    git remote set-url origin "$NEW_URL"
    
    echo "New remote set to:"
    git remote -v
fi