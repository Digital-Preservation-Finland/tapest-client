# vim:ft=spec

%define file_prefix M4_FILE_PREFIX
%define file_ext M4_FILE_EXT

%define file_version M4_FILE_VERSION
%define file_release_tag %{nil}M4_FILE_RELEASE_TAG
%define file_release_number M4_FILE_RELEASE_NUMBER
%define file_build_number M4_FILE_BUILD_NUMBER
%define file_commit_ref M4_FILE_COMMIT_REF

Name:           python3-tapest-ice-api-client
Version:        %{file_version}
Release:        %{file_release_number}%{file_release_tag}.%{file_build_number}.git%{file_commit_ref}%{?dist}
Summary:        ICE API client library for TAPEST
License:        AGPLv3+
URL:            https://www.digitalpreservation.fi
Source0:        ice_api_client.py
Source1:        ice_api_client-METADATA
Source2:        ice_api_client-RECORD
BuildRoot:      %{_tmppath}/%{name}-%{version}-%{release}-root-%(%{__id_u} -n)
BuildArch:      noarch

Requires:       python3
Requires:       python3-requests
Requires:       python3-urllib3

%description
Client library for interacting with the ICE (Ingest, Check, Extract) cold
storage service. Sourced from fairdata/csc-ice and packaged for DPRES.

%prep

%install
mkdir -p %{buildroot}%{python3_sitelib}/ice_api_client
install -m 644 %{SOURCE0} %{buildroot}%{python3_sitelib}/ice_api_client/__init__.py

mkdir -p %{buildroot}%{python3_sitelib}/ice_api_client-%{version}.dist-info
install -m 644 %{SOURCE1} %{buildroot}%{python3_sitelib}/ice_api_client-%{version}.dist-info/METADATA
install -m 644 %{SOURCE2} %{buildroot}%{python3_sitelib}/ice_api_client-%{version}.dist-info/RECORD

%files
%{python3_sitelib}/ice_api_client/
%{python3_sitelib}/ice_api_client-%{version}.dist-info/

# TODO: For now changelog must be last, because it is generated automatically
# from git log command. Appending should be fixed to happen only after %changelog macro
%changelog
