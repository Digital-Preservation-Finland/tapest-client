# vim:ft=spec

%define file_prefix M4_FILE_PREFIX
%define file_ext M4_FILE_EXT
%define file_version M4_FILE_VERSION
%define file_release_tag %{nil}M4_FILE_RELEASE_TAG
%define file_release_number M4_FILE_RELEASE_NUMBER
%define file_build_number M4_FILE_BUILD_NUMBER
%define file_commit_ref M4_FILE_COMMIT_REF

Name:           python3-tapest-client
Version:        %{file_version}
Release:        %{file_release_number}%{file_release_tag}.%{file_build_number}.git%{file_commit_ref}%{?dist}
Summary:        TapeSt API command-line tool and client library
License:        AGPLv3+
URL:            https://www.digitalpreservation.fi
Source0:        %{file_prefix}-v%{file_version}%{?file_release_tag}-%{file_build_number}-g%{file_commit_ref}.%{file_ext}
BuildRoot:      %{_tmppath}/%{name}-%{version}-%{release}-root-%(%{__id_u} -n)
BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  %{py3_dist pip}
BuildRequires:  %{py3_dist setuptools}
BuildRequires:  %{py3_dist setuptools-scm}
BuildRequires:  %{py3_dist wheel}
BuildRequires:  %{py3_dist pytest}
BuildRequires:  %{py3_dist requests-mock}

Requires:       python3-certifi
Requires:       python3-platformdirs
Requires:       python3-tuspy

Obsoletes:      python3-tapest-ice-api-client < %{version}
Provides:       python3-tapest-ice-api-client = %{version}-%{release}

%description
Command-line tool and client library for the TapeSt API service.

%prep
%autosetup -n %{file_prefix}-v%{file_version}%{?file_release_tag}-%{file_build_number}-g%{file_commit_ref}

%build
# TODO: This can be replaced with
# `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_<normalized_upper_dist_name>`
# starting with setuptools-scm 8.0.0.
# Version shipped with RHEL9 is too old.
export SETUPTOOLS_SCM_PRETEND_VERSION=%{file_version}
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files tapest_client

%files -n python3-tapest-client -f %{pyproject_files}
%license LICENSE
%doc README.md
%{_bindir}/tapest-client

# TODO: For now changelog must be last, because it is generated automatically
# from git log command. Appending should be fixed to happen only after %changelog macro
%changelog
