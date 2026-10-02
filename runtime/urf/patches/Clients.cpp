//  Copyright © 2015 Jean-Luc Deltombe (LX3JL). All rights reserved.

// urfd -- The universal reflector
// Copyright © 2021 Thomas A. Early N7TAE
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.


#include "Global.h"

static void AsrLiveEvent(const std::string &json)
{
	static std::mutex mutex;
	std::lock_guard<std::mutex> lock(mutex);
	std::ofstream out("/data/asr-live-events.jsonl", std::ios::app);
	if (out) out << json << std::endl;
}

static bool IsAdminManagedClientProtocol(const std::string &protocol)
{
	return protocol == "DMRMmdvm" ||
	       protocol == "YSF" ||
	       protocol == "P25" ||
	       protocol == "NXDN" ||
	       protocol == "M17";
}

#include "Clients.h"

////////////////////////////////////////////////////////////////////////////////////////
// constructor

CClients::CClients()
{
}

////////////////////////////////////////////////////////////////////////////////////////
// destructors

CClients::~CClients()
{
	m_Mutex.lock();
	m_Clients.clear();
	m_YsfAdminSuppressed.clear();
	m_P25AdminSuppressed.clear();
	m_NxdnAdminSuppressed.clear();
	m_M17AdminSuppressed.clear();
	m_Mutex.unlock();
}

////////////////////////////////////////////////////////////////////////////////////////
// manage Clients

void CClients::AddClient(std::shared_ptr<CClient> client)
{
	// first check if client already exists
	for ( auto it=begin(); it!=end(); it++ )
	{
		if (*client == *(*it))
			// if found, just do nothing
			// so *client keep pointing on a valid object
			// on function return
		{
			// delete new one
			return;
		}
	}

	// and append
	m_Clients.push_back(client);
	std::cout << "New client " << client->GetCallsign() << " at " << client->GetIp() << " added with protocol " << client->GetProtocolName();
	if ( client->GetReflectorModule() != ' ' )
	{
		std::cout << " on module " << client->GetReflectorModule();
	}
	std::cout << std::endl;
	std::ostringstream asrEvent;
	asrEvent << "{\"event\":\"connect\",\"mode\":\"" << client->GetProtocolName() << "\",\"callsign\":\"" << client->GetCallsign().GetCS() << "\",\"module\":\"" << client->GetReflectorModule() << "\",\"epoch\":" << std::time(nullptr) << "}";
	AsrLiveEvent(asrEvent.str());
}

void CClients::RemoveClient(std::shared_ptr<CClient> client)
{
	// look for the client
	bool found = false;
	for ( auto it=begin(); it!=end(); it++ )
	{
		// compare object pointers
		if ( *it == client )
		{
			// found it !
			if ( !(*it)->IsAMaster() )
			{
				// remove it
				std::cout << "Client " << (*it)->GetCallsign() << " at " << (*it)->GetIp() << " removed with protocol " << (*it)->GetProtocolName();
				if ( (*it)->GetReflectorModule() != ' ' )
				{
					std::cout << " on module " << (*it)->GetReflectorModule();
				}
				std::cout << std::endl;
				std::ostringstream asrEvent;
				asrEvent << "{\"event\":\"disconnect\",\"mode\":\"" << (*it)->GetProtocolName() << "\",\"callsign\":\"" << (*it)->GetCallsign().GetCS() << "\",\"module\":\"" << (*it)->GetReflectorModule() << "\",\"epoch\":" << std::time(nullptr) << "}";
				AsrLiveEvent(asrEvent.str());
				m_Clients.erase(it);
				break;
			}
		}
	}
}

bool CClients::IsClient(std::shared_ptr<CClient> client) const
{
	for ( auto it=cbegin(); it!=cend(); it++ )
	{
		if (*it == client)
			return true;
	}
	return false;
}


int CClients::AdminDisconnect(const std::string &callsign, const std::string &protocol, const std::string &event)
{
	int removed = 0;
	const bool prefix = !callsign.empty() && callsign.back() == '*';
	const std::string wanted = prefix ? callsign.substr(0, callsign.size() - 1) : callsign;
	for (auto it=m_Clients.begin(); it!=m_Clients.end(); )
	{
		auto client = *it;
		std::string clientCall = client->GetCallsign().GetBase();
		std::string clientProtocol = client->GetProtocolName();
		const bool callsignMatches = prefix ? clientCall.compare(0, wanted.size(), wanted) == 0 : clientCall == wanted;
		const bool protocolMatches = clientProtocol == protocol ||
			(protocol == "*" && IsAdminManagedClientProtocol(clientProtocol));
		if (callsignMatches && protocolMatches && !client->IsAMaster())
		{
			std::cout << "Admin disconnect client " << client->GetCallsign() << " at " << client->GetIp() << " protocol " << clientProtocol << std::endl;
			if (event == "ban" && clientProtocol == "YSF" && !IsYsfAdminSuppressed(client->GetIp()))
				m_YsfAdminSuppressed.push_back({client->GetIp(), clientCall, event});
			if (event == "ban" && clientProtocol == "P25" && !IsP25AdminSuppressed(client->GetIp()))
				m_P25AdminSuppressed.push_back({client->GetIp(), clientCall, event});
			if (event == "ban" && clientProtocol == "NXDN" && !IsNxdnAdminSuppressed(client->GetIp()))
				m_NxdnAdminSuppressed.push_back({client->GetIp(), clientCall, event});
			if (event == "ban" && clientProtocol == "M17" && !IsM17AdminSuppressed(client->GetIp()))
				m_M17AdminSuppressed.push_back({client->GetIp(), clientCall, event});
			std::ostringstream asrEvent;
			asrEvent << "{\"event\":\"" << event << "\",\"mode\":\"" << clientProtocol << "\",\"callsign\":\"" << clientCall << "\",\"module\":\"" << client->GetReflectorModule() << "\",\"reason\":\"admin\",\"epoch\":" << std::time(nullptr) << "}";
			AsrLiveEvent(asrEvent.str());
			it = m_Clients.erase(it);
			removed++;
		}
		else ++it;
	}
	return removed;
}

int CClients::AdminClearYsfBanSuppression(const std::string &callsign)
{
	int cleared = 0;
	const bool prefix = !callsign.empty() && callsign.back() == '*';
	const std::string wanted = prefix ? callsign.substr(0, callsign.size() - 1) : callsign;
	for (auto it=m_YsfAdminSuppressed.begin(); it!=m_YsfAdminSuppressed.end(); )
	{
		const bool callsignMatches = prefix ? it->callsign.compare(0, wanted.size(), wanted) == 0 : it->callsign == wanted;
		if (it->reason == "ban" && callsignMatches)
		{
			it = m_YsfAdminSuppressed.erase(it);
			cleared++;
		}
		else
			++it;
	}
	return cleared;
}

bool CClients::IsYsfAdminSuppressed(const CIp &ip) const
{
	for (const auto &suppressed : m_YsfAdminSuppressed)
	{
		if (suppressed.ip == ip)
			return true;
	}
	return false;
}

void CClients::ClearYsfAdminSuppression(const CIp &ip)
{
	for (auto it=m_YsfAdminSuppressed.begin(); it!=m_YsfAdminSuppressed.end(); )
	{
		// A genuine unlink completes a one-session Kick. Ban suppression is
		// intentionally retained until the matching Global Ban is removed.
		if (it->reason == "kick" && it->ip == ip)
			it = m_YsfAdminSuppressed.erase(it);
		else
			++it;
	}
}

int CClients::AdminClearP25BanSuppression(const std::string &callsign)
{
	int cleared = 0;
	const bool prefix = !callsign.empty() && callsign.back() == '*';
	const std::string wanted = prefix ? callsign.substr(0, callsign.size() - 1) : callsign;
	for (auto it=m_P25AdminSuppressed.begin(); it!=m_P25AdminSuppressed.end(); )
	{
		const bool callsignMatches = prefix ? it->callsign.compare(0, wanted.size(), wanted) == 0 : it->callsign == wanted;
		if (it->reason == "ban" && callsignMatches)
		{
			it = m_P25AdminSuppressed.erase(it);
			cleared++;
		}
		else
			++it;
	}
	return cleared;
}

bool CClients::IsP25AdminSuppressed(const CIp &ip) const
{
	for (const auto &suppressed : m_P25AdminSuppressed)
	{
		if (suppressed.ip == ip)
			return true;
	}
	return false;
}

void CClients::ClearP25AdminSuppression(const CIp &ip)
{
	for (auto it=m_P25AdminSuppressed.begin(); it!=m_P25AdminSuppressed.end(); )
	{
		// A genuine P25 disconnect completes a one-session Kick. Ban
		// suppression remains until Global Unban explicitly removes it.
		if (it->reason == "kick" && it->ip == ip)
			it = m_P25AdminSuppressed.erase(it);
		else
			++it;
	}
}

int CClients::AdminClearNxdnBanSuppression(const std::string &callsign)
{
	int cleared = 0;
	const bool prefix = !callsign.empty() && callsign.back() == '*';
	const std::string wanted = prefix ? callsign.substr(0, callsign.size() - 1) : callsign;
	for (auto it=m_NxdnAdminSuppressed.begin(); it!=m_NxdnAdminSuppressed.end(); )
	{
		const bool callsignMatches = prefix ? it->callsign.compare(0, wanted.size(), wanted) == 0 : it->callsign == wanted;
		if (it->reason == "ban" && callsignMatches)
		{
			it = m_NxdnAdminSuppressed.erase(it);
			cleared++;
		}
		else
			++it;
	}
	return cleared;
}

bool CClients::IsNxdnAdminSuppressed(const CIp &ip) const
{
	for (const auto &suppressed : m_NxdnAdminSuppressed)
	{
		if (suppressed.ip == ip)
			return true;
	}
	return false;
}

void CClients::ClearNxdnAdminSuppression(const CIp &ip)
{
	for (auto it=m_NxdnAdminSuppressed.begin(); it!=m_NxdnAdminSuppressed.end(); )
	{
		// A genuine NXDN disconnect completes a one-session Kick. Ban
		// suppression remains until Global Unban explicitly removes it.
		if (it->reason == "kick" && it->ip == ip)
			it = m_NxdnAdminSuppressed.erase(it);
		else
			++it;
	}
}

int CClients::AdminClearM17BanSuppression(const std::string &callsign)
{
	int cleared = 0;
	const bool prefix = !callsign.empty() && callsign.back() == '*';
	const std::string wanted = prefix ? callsign.substr(0, callsign.size() - 1) : callsign;
	for (auto it=m_M17AdminSuppressed.begin(); it!=m_M17AdminSuppressed.end(); )
	{
		const bool callsignMatches = prefix ? it->callsign.compare(0, wanted.size(), wanted) == 0 : it->callsign == wanted;
		if (it->reason == "ban" && callsignMatches)
		{
			it = m_M17AdminSuppressed.erase(it);
			cleared++;
		}
		else
			++it;
	}
	return cleared;
}

bool CClients::IsM17AdminSuppressed(const CIp &ip) const
{
	for (const auto &suppressed : m_M17AdminSuppressed)
	{
		if (suppressed.ip == ip)
			return true;
	}
	return false;
}

void CClients::ClearM17AdminSuppression(const CIp &ip)
{
	for (auto it=m_M17AdminSuppressed.begin(); it!=m_M17AdminSuppressed.end(); )
	{
		// A genuine M17 disconnect completes a one-session Kick. Ban
		// suppression remains until Global Unban explicitly removes it.
		if (it->reason == "kick" && it->ip == ip)
			it = m_M17AdminSuppressed.erase(it);
		else
			++it;
	}
}

////////////////////////////////////////////////////////////////////////////////////////
// find Clients

std::shared_ptr<CClient> CClients::FindClient(const CIp &Ip)
{
	// find client
	for ( auto it=begin(); it!=end(); it++ )
	{
		if ( (*it)->GetIp() == Ip )
		{
			return *it;
		}
	}

	// done
	return nullptr;
}

std::shared_ptr<CClient> CClients::FindClient(const CIp &Ip, const EProtocol Protocol)
{
	// find client
	for ( auto it=begin(); it!=end(); it++ )
	{
		if ( ((*it)->GetIp() == Ip)  && ((*it)->GetProtocol() == Protocol))
		{
			return *it;
		}
	}

	// done
	return nullptr;
}

std::shared_ptr<CClient> CClients::FindClient(const CIp &Ip, const EProtocol Protocol, const char ReflectorModule)
{
	// find client
	for ( auto it=begin(); it!=end(); it++ )
	{
		if ( ((*it)->GetIp() == Ip)  && ((*it)->GetReflectorModule() == ReflectorModule) && ((*it)->GetProtocol() == Protocol) )
		{
			return *it;
		}
	}

	// done
	return nullptr;
}

std::shared_ptr<CClient> CClients::FindClient(const CCallsign &Callsign, const CIp &Ip, const EProtocol Protocol)
{
	// find client
	for ( auto it=begin(); it!=end(); it++ )
	{
		if ( (*it)->GetCallsign().HasSameCallsign(Callsign) && ((*it)->GetIp() == Ip)  && ((*it)->GetProtocol() == Protocol) )
		{
			return *it;
		}
	}

	return nullptr;
}

std::shared_ptr<CClient> CClients::FindClient(const CCallsign &Callsign, char module, const CIp &Ip, const EProtocol Protocol)
{
	// find client
	for ( auto it=begin(); it!=end(); it++ )
	{
		if ( (*it)->GetCallsign().HasSameCallsign(Callsign) && ((*it)->GetCSModule() == module) && ((*it)->GetIp() == Ip)  && ((*it)->GetProtocol() == Protocol) )
		{
			return *it;
		}
	}

	return nullptr;
}

std::shared_ptr<CClient> CClients::FindClient(const CCallsign &Callsign, const EProtocol Protocol)
{
	// find client
	for ( auto it=begin(); it!=end(); it++ )
	{
		if ( ((*it)->GetProtocol() == Protocol) && (*it)->GetCallsign().HasSameCallsign(Callsign) )
		{
			return *it;
		}
	}

	return nullptr;
}

////////////////////////////////////////////////////////////////////////////////////////
// iterate on clients

std::shared_ptr<CClient> CClients::FindNextClient(const EProtocol Protocol, std::list<std::shared_ptr<CClient>>::iterator &it)
{
	while ( it != end() )
	{
		if ( (*it)->GetProtocol() == Protocol )
		{
			return *it++;
		}
		it++;
	}
	return nullptr;
}

std::shared_ptr<CClient> CClients::FindNextClient(const CIp &Ip, const EProtocol Protocol, std::list<std::shared_ptr<CClient>>::iterator &it)
{
	while ( it != end() )
	{
		if ( ((*it)->GetProtocol() == Protocol) && ((*it)->GetIp() == Ip) )
		{
			return *it++;
		}
		it++;
	}
	return nullptr;
}

std::shared_ptr<CClient> CClients::FindNextClient(const CCallsign &Callsign, const CIp &Ip, const EProtocol Protocol, std::list<std::shared_ptr<CClient>>::iterator &it)
{
	while ( it != end() )
	{
		if ( ((*it)->GetProtocol() == Protocol) && ((*it)->GetIp() == Ip) && (*it)->GetCallsign().HasSameCallsign(Callsign) )
		{
			return *it++;
		}
		it++;
	}
	return nullptr;
}
