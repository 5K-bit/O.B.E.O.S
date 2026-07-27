local data = require('data.min')
local battery = require('battery.min')
local camera = require('camera.min')
local code = require('code.min')
local plain_text = require('plain_text.min')
local tap = require('tap.min')
local imu = require('imu.min')

TEXT_MSG = 0x0b
CAPTURE_SETTINGS_MSG = 0x0d
AUDIO_SUBS_MSG = 0x30
TAP_SUBS_MSG = 0x10
IMU_SUBS_MSG = 0x40
IMU_DATA_MSG = 0x0a
AUDIO_DATA_NON_FINAL_MSG = 0x05
AUDIO_DATA_FINAL_MSG = 0x06

USE_PARSERS = data.parsers ~= nil

if USE_PARSERS then
    data.parsers[TEXT_MSG] = plain_text.parse_plain_text
    data.parsers[CAPTURE_SETTINGS_MSG] = camera.parse_capture_settings
    data.parsers[AUDIO_SUBS_MSG] = code.parse_code
    data.parsers[TAP_SUBS_MSG] = code.parse_code
    data.parsers[IMU_SUBS_MSG] = code.parse_code
end

function clear_display()
    frame.display.text(' ', 1, 1)
    frame.display.show()
    frame.sleep(0.04)
end

function print_text(text_obj)
    clear_display()
    local i = 0
    for line in text_obj.string:gmatch('([^\n]*)\n?') do
        if line ~= '' then
            frame.display.text(line, 1, i * 52 + 1)
            i = i + 1
        end
    end
    frame.display.show()
end

function run_auto_exp_if_elapsed(previous, interval)
    local t = frame.time.utc()
    if previous == 0 or ((t - previous) > interval) then
        camera.run_auto_exposure()
        return t
    end
    return previous
end

function handle_tap_subscription(msg)
    if msg.value == 1 then
        frame.imu.tap_callback(tap.send_tap)
    else
        frame.imu.tap_callback(nil)
    end
end

function handle_audio_subscription(msg)
    if msg.value == 1 then
        pcall(frame.microphone.start, {sample_rate=8000, bit_depth=16})
        return true
    end
    pcall(frame.microphone.stop)
    return false
end

function handle_parser_items()
    local items_ready = data.process_raw_items()
    local audio_change = nil
    local imu_change = nil
    if items_ready > 0 then
        if data.app_data[TEXT_MSG] ~= nil and data.app_data[TEXT_MSG].string ~= nil then
            print_text(data.app_data[TEXT_MSG])
            data.app_data[TEXT_MSG] = nil
        end

        if data.app_data[TAP_SUBS_MSG] ~= nil then
            handle_tap_subscription(data.app_data[TAP_SUBS_MSG])
            data.app_data[TAP_SUBS_MSG] = nil
        end

        if data.app_data[IMU_SUBS_MSG] ~= nil then
            imu_change = data.app_data[IMU_SUBS_MSG].value == 1
            data.app_data[IMU_SUBS_MSG] = nil
        end

        if data.app_data[CAPTURE_SETTINGS_MSG] ~= nil then
            local ok, capture_err = pcall(camera.capture_and_send, data.app_data[CAPTURE_SETTINGS_MSG])
            if ok == false then
                print(capture_err)
            end
            data.app_data[CAPTURE_SETTINGS_MSG] = nil
        end

        if data.app_data[AUDIO_SUBS_MSG] ~= nil then
            audio_change = handle_audio_subscription(data.app_data[AUDIO_SUBS_MSG])
            data.app_data[AUDIO_SUBS_MSG] = nil
        end
    end
    return audio_change, imu_change
end

function handle_queue_items()
    local items = data.process_raw_items()
    local audio_change = nil
    local imu_change = nil
    for i = 1, #items do
        local flag = items[i][1]
        local raw = items[i][2]
        if flag == TEXT_MSG then
            local text_obj = plain_text.parse_plain_text(raw)
            if text_obj ~= nil and text_obj.string ~= nil then
                print_text(text_obj)
            end
        elseif flag == TAP_SUBS_MSG then
            handle_tap_subscription(code.parse_code(raw))
        elseif flag == IMU_SUBS_MSG then
            imu_change = code.parse_code(raw).value == 1
        elseif flag == CAPTURE_SETTINGS_MSG then
            local ok, capture_err = pcall(camera.capture_and_send, camera.parse_capture_settings(raw))
            if ok == false then
                print(capture_err)
            end
        elseif flag == AUDIO_SUBS_MSG then
            audio_change = handle_audio_subscription(code.parse_code(raw))
        end
    end
    return audio_change, imu_change
end

function app_loop()
    clear_display()
    frame.display.text('O.B.E.O.S Frame', 1, 1)
    frame.display.show()
    print('Frame app is running')

    local last_batt_update = 0
    local last_auto_exp = 0
    local audio_streaming = false
    local imu_streaming = false
    local audio_data = ''
    local mtu = frame.bluetooth.max_length()
    if mtu % 2 == 1 then
        mtu = mtu - 1
    end

    while true do
        rc, err = pcall(function()
            local audio_change = nil
            local imu_change = nil
            if USE_PARSERS then
                audio_change, imu_change = handle_parser_items()
            else
                audio_change, imu_change = handle_queue_items()
            end
            if audio_change ~= nil then
                audio_data = ''
                audio_streaming = audio_change
            end
            if imu_change ~= nil then
                imu_streaming = imu_change
            end
        end)

        if rc == false then
            print(err)
            clear_display()
            break
        end

        if audio_streaming then
            for i = 1, 20 do
                audio_data = frame.microphone.read(mtu)
                if audio_data == nil then
                    pcall(frame.bluetooth.send, string.char(AUDIO_DATA_FINAL_MSG))
                    frame.sleep(0.0025)
                    audio_streaming = false
                    break
                elseif audio_data ~= '' then
                    pcall(frame.bluetooth.send, string.char(AUDIO_DATA_NON_FINAL_MSG) .. audio_data)
                    frame.sleep(0.0025)
                end
            end
        end

        if imu_streaming then
            imu.send_imu_data(IMU_DATA_MSG)
        end

        last_auto_exp = run_auto_exp_if_elapsed(last_auto_exp, 0.1)
        last_batt_update = battery.send_batt_if_elapsed(last_batt_update, 120)
        if not audio_streaming then
            frame.sleep(0.1)
        end
    end
end

app_loop()
