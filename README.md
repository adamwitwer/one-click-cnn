# One-Touch CNN 🧞📺

A modern, Flask-based web application that simplifies your TV experience. Launch the CNN app on your Roku and toggle your TV's mute status—all with a single tap from your smartphone.

## ✨ Features

-   **One-Touch Launch**: Instantly start the CNN app on your Roku device.
-   **Mute Toggle**: Quickly mute or unmute your Samsung TV via SmartThings.
-   **Smart Automation**: Automatically dismisses "Are you still watching?" overlays and other pop-ups.
-   **Responsive Design**: Beautiful, dark-mode interface that looks great on any mobile device.
-   **Real-time Feedback**: Visual loading indicators keep you informed while the magic happens.

## 🛠️ Prerequisites

-   **Python 3.8+**
-   **Roku Device**: TV or Streaming Stick (Developer mode not required).
-   **Samsung TV** (Optional): For mute functionality via SmartThings.

## 🚀 Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/adamwitwer/one-click-cnn.git
    cd one-click-cnn
    ```

2.  **Set up a virtual environment:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

## ⚙️ Configuration

1.  Create a `.env` file in the root directory:
    ```bash
    cp .env.example .env
    ```

2.  Update the `.env` file with your device details:
    ```ini
    # Roku Configuration
    ROKU_IP=192.168.1.100  # Find this in Roku Settings > Network

    # SmartThings Configuration (Optional - for TV control)
    SMARTTHINGS_CLIENT_ID=your_client_id
    SMARTTHINGS_CLIENT_SECRET=your_client_secret
    SMARTTHINGS_TV_DEVICE_ID=your_device_id
    ```

    *Note: SmartThings tokens are managed automatically after the first run.*

## 📱 Usage

1.  **Start the application:**
    ```bash
    ./run.sh
    ```

2.  **Access the interface:**
    Open your web browser and navigate to `http://localhost:5000` (or your server's IP address).

3.  **Add to Home Screen:**
    For the best experience on iOS/Android, use "Add to Home Screen" to install it as a web app.
