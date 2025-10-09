#!/bin/bash

set -euo pipefail

DOMAIN_PATTERNS=("openshift-ci.odhdev.com" "openshift-ci-aws.rhaiseng.com")
REGEX_PATTERNS=("^[0-9a-z]{20}\.hypershift\.local$")
DRY_RUN=${DRY_RUN:-true}
ZONE_TYPE=${ZONE_TYPE:-"all"}  # all, public, private

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >&2
}

confirm_deletion() {
    local zone_name="$1"
    local zone_id="$2"

    if [[ "$DRY_RUN" == "true" ]]; then
        log "DRY RUN: Would delete hosted zone: $zone_name (ID: $zone_id)"
        return 0
    fi
    return 0

    echo "Are you sure you want to delete hosted zone '$zone_name' (ID: $zone_id)? [y/N]"
    read -r response
    case "$response" in
        [yY][eE][sS]|[yY])
            return 0
            ;;
        *)
            log "Skipping deletion of $zone_name"
            return 1
            ;;
    esac
}

delete_hosted_zone() {
    local zone_name="$1"
    local zone_id="$2"

    if [[ "$DRY_RUN" == "true" ]]; then
        return 0
    fi

    log "Deleting hosted zone: $zone_name (ID: $zone_id)"

    log "First, deleting all records except NS and SOA..."
    aws route53 list-resource-record-sets --hosted-zone-id "$zone_id" \
        --query "ResourceRecordSets[?Type != 'NS' && Type != 'SOA']" \
        --output json | jq -r '.[] | @base64' | while read -r record; do

        decoded=$(echo "$record" | base64 --decode)
        record_name=$(echo "$decoded" | jq -r '.Name')
        record_type=$(echo "$decoded" | jq -r '.Type')

        log "Deleting record: $record_name ($record_type)"

        change_batch=$(echo "$decoded" | jq '{
            Comment: "Cleanup before zone deletion",
            Changes: [{
                Action: "DELETE",
                ResourceRecordSet: .
            }]
        }')

        aws route53 change-resource-record-sets \
            --hosted-zone-id "$zone_id" \
            --change-batch "$change_batch" >/dev/null || {
            log "Warning: Failed to delete record $record_name ($record_type), continuing..."
        }
    done

    log "Deleting hosted zone: $zone_id"
    aws route53 delete-hosted-zone --id "$zone_id" >/dev/null
    log "Successfully deleted hosted zone: $zone_name"
}

main() {
    log "Starting AWS hosted zone cleanup"
    log "Domain patterns: ${DOMAIN_PATTERNS[*]}"
    log "Regex patterns: ${REGEX_PATTERNS[*]}"
    log "Zone type filter: $ZONE_TYPE"
    log "DRY RUN mode: $DRY_RUN"

    if ! command -v aws >/dev/null 2>&1; then
        log "ERROR: AWS CLI not found. Please install it first."
        exit 1
    fi

    if ! command -v jq >/dev/null 2>&1; then
        log "ERROR: jq not found. Please install it first."
        exit 1
    fi

    log "Fetching hosted zones (type: $ZONE_TYPE)..."

    case "$ZONE_TYPE" in
        "public")
            hosted_zones=$(aws route53 list-hosted-zones --query 'HostedZones[?Config.PrivateZone==`false`]' --output json)
            ;;
        "private")
            hosted_zones=$(aws route53 list-hosted-zones --query 'HostedZones[?Config.PrivateZone==`true`]' --output json)
            ;;
        "all")
            hosted_zones=$(aws route53 list-hosted-zones --query 'HostedZones' --output json)
            ;;
        *)
            log "ERROR: Invalid ZONE_TYPE '$ZONE_TYPE'. Must be 'all', 'public', or 'private'."
            exit 1
            ;;
    esac

    zones_to_delete=()

    # Parse zones using jq array indexing
    zone_count=$(echo "$hosted_zones" | jq '. | length')
    log "Found $zone_count total hosted zones"

    for ((i=0; i<zone_count; i++)); do
        zone_name=$(echo "$hosted_zones" | jq -r ".[$i].Name" | sed 's/\.$//')
        zone_id=$(echo "$hosted_zones" | jq -r ".[$i].Id" | sed 's|/hostedzone/||')
        zone_private=$(echo "$hosted_zones" | jq -r ".[$i].Config.PrivateZone")
        zone_type=$(if [[ "$zone_private" == "true" ]]; then echo "private"; else echo "public"; fi)

        log "Processing zone: $zone_name (type: $zone_type)"

        # Skip zones that start with "openshift"
        if [[ "$zone_name" =~ ^openshift ]]; then
            log "Skipping zone (starts with 'openshift'): $zone_name"
        else
            matched=false

            # Check string patterns
            for pattern in "${DOMAIN_PATTERNS[@]}"; do
                if [[ "$zone_name" == *"$pattern"* ]]; then
                    log "Found matching zone (string pattern): $zone_name (ID: $zone_id, type: $zone_type)"
                    zones_to_delete+=("$zone_name|$zone_id|$zone_type")
                    matched=true
                    break
                fi
            done

            # Check regex patterns if not already matched
            if [[ "$matched" == false ]]; then
                for regex in "${REGEX_PATTERNS[@]}"; do
                    if [[ "$zone_name" =~ $regex ]]; then
                        log "Found matching zone (regex pattern): $zone_name (ID: $zone_id, type: $zone_type)"
                        zones_to_delete+=("$zone_name|$zone_id|$zone_type")
                        break
                    fi
                done
            fi
        fi
    done

    if [[ ${#zones_to_delete[@]} -eq 0 ]]; then
        log "No hosted zones found matching the specified patterns."
        exit 0
    fi

    log "Found ${#zones_to_delete[@]} hosted zone(s) to delete"

    for zone_info in "${zones_to_delete[@]}"; do
        IFS='|' read -r zone_name zone_id zone_type <<< "$zone_info"

        if confirm_deletion "$zone_name" "$zone_id"; then
            delete_hosted_zone "$zone_name" "$zone_id"
        fi
    done

    log "Hosted zone cleanup completed"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi