#pragma once

// tcd - a hybid transcoder using DVSI hardware and Codec2 software
// Copyright © 2021 Thomas A. Early N7TAE
// Copyright © 2021 Doug McLain AD8DP

// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.

// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.

// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

#include <unordered_map>
#include <memory>
#include <atomic>
#include <future>
#include <mutex>
#include <list>
#include <utility>
#include <imbe_vocoder_api.h>

#ifdef SW_MODES_ONLY
#include "PacketQueue.h"
#endif
#include "codec2.h"
#include "DV3000.h"
#include "DV3003.h"
#include "TCSocket.h"
#include "AsrPcmMeter.h"

class CController
{
public:
	std::mutex dstar_mux, dmrst_mux;

	CController();
	bool Start();
	void Stop();
	void RouteDstPacket(std::shared_ptr<CTranscoderPacket> packet);
	void RouteDmrPacket(std::shared_ptr<CTranscoderPacket> packet);
	void Dump(const std::shared_ptr<CTranscoderPacket> packet, const std::string &title) const;

protected:
	std::atomic<bool> keep_running;
	std::future<void> reflectorFuture, c2Future, imbeFuture, usrpFuture, asrMeterFuture;
	std::unordered_map<char, int16_t[160]> audio_store;
	std::unordered_map<char, uint8_t[8]> data_store;
	CTCClient tcClient;
	std::unordered_map<char, std::unique_ptr<CCodec2>> c2_16, c2_32;
#ifndef SW_MODES_ONLY
	std::unique_ptr<CDVDevice> dstar_device, dmrsf_device;
#endif
	CPacketQueue codec2_queue;

	CPacketQueue imbe_queue;
	CPacketQueue usrp_queue;
	std::mutex send_mux;
	int32_t ambe_in_num, ambe_out_num, usrp_rx_num, usrp_tx_num;
	CAsrPcmMeter asr_pcm_meter;
	imbe_vocoder p25vocoder;

	int32_t calcNumerator(float db) const;
#ifndef SW_MODES_ONLY
	bool DiscoverFtdiDevices(std::list<std::pair<std::string, std::string>> &found);
#endif
	bool InitVocoders();
	// processing threads
	void ReadReflectorThread();
	void ProcessC2Thread();

	void ProcessIMBEThread();
	void ProcessUSRPThread();
    void AsrMeterPublisherThread();
	void Codec2toAudio(std::shared_ptr<CTranscoderPacket> packet);
	void AudiotoCodec2(std::shared_ptr<CTranscoderPacket> packet);
	void IMBEtoAudio(std::shared_ptr<CTranscoderPacket> packet);
	void AudiotoIMBE(std::shared_ptr<CTranscoderPacket> packet);
	void USRPtoAudio(std::shared_ptr<CTranscoderPacket> packet);
	void AudiotoUSRP(std::shared_ptr<CTranscoderPacket> packet);
	void SendToReflector(std::shared_ptr<CTranscoderPacket> packet);
#ifdef USE_SW_AMBE2
    std::future<void> swambe2Future;
    CPacketQueue swambe2_queue;
    void ProcessSWAMBE2Thread();
    void SWAMBE2toAudio(std::shared_ptr<CTranscoderPacket> packet);
    void AudiotoSWAMBE2(std::shared_ptr<CTranscoderPacket> packet);
#endif
};
