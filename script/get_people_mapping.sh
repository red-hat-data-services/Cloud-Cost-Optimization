#!/bin/bash

SEARCH=$@
if [ "$1" = "--mutt" ]; then
	MUTT="yes"
	SEARCH=$2 $3 $4 $5
fi
if [ "$1" = "--recursive" ] || [ "$1" = "-r" ]; then
	RECURSIVE="yes"
	SEARCH=$2 $3 $4 $5
fi

FIELDS="cn rhatJobTitle mail manager rhatBio rhatLocation rhatCostCenterDesc rhatRnDComponent rhatProject rhatSocialURL rhatOriginalHireDate"
RESULT=$(ldapsearch -x -o ldif_wrap=no -LLL "(|(uid=$SEARCH)(cn=$SEARCH))" $FIELDS)

# If there are no direct results, do a wide search with limited output
if [ "$RESULT" == "" ] || [ "$MUTT" == "yes" ]; then
	RESULT=$(ldapsearch -x -o ldif_wrap=no -LLL "(|(uid=$SEARCH*)(cn=*$SEARCH*))" uid mail cn)
	for line in "$RESULT"; do
		echo $line | sed 's/dn:/\n/g' | sed -E 's/.*mail: (.*)/\1/' | sed -E 's/ cn: /\t/'
	done
	exit 0
fi

# Format hire date to be readable
HIRE="$(echo "$RESULT" | grep "rhatOriginalHireDate" | awk '{print $2}')"
RESULT=$(echo "$RESULT" | sed -E "s/(rhatOriginalHireDate: ).*/\1$(date --date="${HIRE:0:8}" +%F)/")

# Improve output formatting
DETAILS=$(echo "$RESULT" | grep -v "rhatSocialURL:" | sed -E 's/^rhat(Job|Cost|RnD|Original)?//' | sed -E 's/D(\w*):/:/' | sed -E 's/\w/\L&/')
#echo "$DETAILS" | sed -E 's/: /:     \t/'

# Format social links
SOCIAL=$(echo "$RESULT" | grep "rhatSocialURL" | sed -E 's/rhatSocialURL: (.*)/- \1/g' | sort | sed -E 's/->/: /g')
#[ "$SOCIAL" != "" ] && echo -e "social:\n$SOCIAL"

# Function to print reports
reports()
{
	local level=$1
	local dn=$2
	local records=$(ldapsearch -x -o ldif_wrap=no -LLL "manager=$dn" uid mail cn rhatJobTitle)
	local directs=$(echo $records | sed 's/dn:/\n/g' | sed -E 's/.*mail: (.*)/\1/' | sed -E 's/ cn: /\t/' | sed -E 's/ rhatJobTitle: (.*)/\t(\1)/' | sed -E 's/(\w) \)/\1)/')
	while read direct
	do
		if [ "$direct" != "" ]; then
			# Ensure people don't report to themselves in the output
			local uid=$(echo $direct | awk -F@ '{print $1}')
			local dnuid=$(echo $dn | awk -F, '{print $1}' | sed 's/uid=//')
			if [ "$uid" != "$dnuid" ]; then
				# Tabs based on the report hierarchy
				# for ((i=0; i<$level; i++)); do
				#	echo -ne "\t"
				# done
				employeeDetails=($direct)
				employeeEmail=${employeeDetails[0]}
				echo $employeeEmail:$dnuid@redhat.com

				# Call the direct reports of the current entry
				if [ "$RECURSIVE" == "yes" ] && [ "$direct" != "" ]; then
					reports $(echo "$level+1"|bc) "uid=$uid,ou=users,dc=redhat,dc=com"
				fi
			fi
		fi
	done <<< $directs
}

#echo ""

# Print direct reports based on the DN
DN="$(echo "$RESULT" | grep "^dn:" | awk '{print $2}')"
reports 0 $DN
