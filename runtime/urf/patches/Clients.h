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

#pragma once

#include "Client.h"


////////////////////////////////////////////////////////////////////////////////////////
// define


////////////////////////////////////////////////////////////////////////////////////////
// class

class CClients
{
public:
	// constructors
	CClients();

	// destructors
	virtual ~CClients();

	// locks
	void Lock(void)                     { m_Mutex.lock(); }
	void Unlock(void)                   { m_Mutex.unlock(); }

	// manage Clients
	int     GetSize(void) const         { return (int)m_Clients.size(); }
	void    AddClient(std::shared_ptr<CClient>);
	void    RemoveClient(std::shared_ptr<CClient>);
	int     AdminDisconnect(const std::string &, const std::string &, const std::string &);
	int     AdminClearYsfBanSuppression(const std::string &);
	bool    IsYsfAdminSuppressed(const CIp &) const;
	void    ClearYsfAdminSuppression(const CIp &);
	int     AdminClearP25BanSuppression(const std::string &);
	bool    IsP25AdminSuppressed(const CIp &) const;
	void    ClearP25AdminSuppression(const CIp &);
	int     AdminClearNxdnBanSuppression(const std::string &);
	bool    IsNxdnAdminSuppressed(const CIp &) const;
	void    ClearNxdnAdminSuppression(const CIp &);
	int     AdminClearM17BanSuppression(const std::string &);
	bool    IsM17AdminSuppressed(const CIp &) const;
	void    ClearM17AdminSuppression(const CIp &);
	bool    IsClient(std::shared_ptr<CClient>) const;

	// pass-through
	std::list<std::shared_ptr<CClient>>::iterator begin()              { return m_Clients.begin(); }
	std::list<std::shared_ptr<CClient>>::iterator end()                { return m_Clients.end(); }
	std::list<std::shared_ptr<CClient>>::const_iterator cbegin() const { return m_Clients.cbegin(); }
	std::list<std::shared_ptr<CClient>>::const_iterator cend()   const { return m_Clients.cend(); }

	// find clients
	std::shared_ptr<CClient> FindClient(const CIp &);
	std::shared_ptr<CClient> FindClient(const CIp &, const EProtocol);
	std::shared_ptr<CClient> FindClient(const CIp &, const EProtocol, const char);
	std::shared_ptr<CClient> FindClient(const CCallsign &, const CIp &, const EProtocol);
	std::shared_ptr<CClient> FindClient(const CCallsign &, char, const CIp &, const EProtocol);
	std::shared_ptr<CClient> FindClient(const CCallsign &, const EProtocol);

	// iterate on clients
	std::shared_ptr<CClient> FindNextClient(const EProtocol, std::list<std::shared_ptr<CClient>>::iterator &);
	std::shared_ptr<CClient> FindNextClient(const CIp &, const EProtocol, std::list<std::shared_ptr<CClient>>::iterator &);
	std::shared_ptr<CClient> FindNextClient(const CCallsign &, const CIp &, const EProtocol, std::list<std::shared_ptr<CClient>>::iterator &);

protected:
	struct YsfAdminSuppression
	{
		CIp ip;
		std::string callsign;
		std::string reason;
	};

	struct P25AdminSuppression
	{
		CIp ip;
		std::string callsign;
		std::string reason;
	};

	struct NxdnAdminSuppression
	{
		CIp ip;
		std::string callsign;
		std::string reason;
	};

	struct M17AdminSuppression
	{
		CIp ip;
		std::string callsign;
		std::string reason;
	};

	// data
	std::mutex           m_Mutex;
	std::list<std::shared_ptr<CClient>> m_Clients;
	std::list<YsfAdminSuppression> m_YsfAdminSuppressed;
	std::list<P25AdminSuppression> m_P25AdminSuppressed;
	std::list<NxdnAdminSuppression> m_NxdnAdminSuppressed;
	std::list<M17AdminSuppression> m_M17AdminSuppressed;
};
