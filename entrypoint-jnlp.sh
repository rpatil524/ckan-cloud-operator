#!/usr/bin/env bash

[ -z "${CKAN_CLOUD_USER_NAME}" ] && echo missing CKAN_CLOUD_USER_NAME && exit 1
! [ -e /etc/ckan-cloud/.kube-config ] && echo missing /etc/ckan-cloud/.kube-config && exit 1
! [ -z "${KUBE_CONTEXT}" ] && kubectl config use-context "${KUBE_CONTEXT}" >/dev/null 2>&1
export KUBECONFIG=/etc/ckan-cloud/.kube-config
USER_KUBECONFIG=`mktemp`
ckan-cloud-operator activate-gcloud-auth >/dev/null 2>&1 &&\
ckan-cloud-operator users get-kubeconfig "${CKAN_CLOUD_USER_NAME}" > "${USER_KUBECONFIG}"
[ "$?" != "0" ] && echo failed to get authentication credentials && exit 1

rm -rf .config/gcloud

export KUBECONFIG="${USER_KUBECONFIG}"

if [ "$*" == "" ]; then
    exec jenkins-slave
else
    exec "$@"
fi
