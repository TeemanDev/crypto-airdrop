document.getElementById('airdropForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    
    const walletAddress = document.getElementById('wallet_address').value.trim();
    const email = document.getElementById('email').value.trim();
    const twitterHandle = document.getElementById('twitter_handle').value.trim();
    const referralCode = document.getElementById('referral_code').value.trim();
    
    // Basic validation
    if (!walletAddress) {
        showMessage('Wallet address is required', 'error');
        return;
    }
    
    // Validate wallet format
    if (!walletAddress.startsWith('0x') || walletAddress.length !== 42) {
        showMessage('Invalid wallet address format. Must start with 0x and be 42 characters.', 'error');
        return;
    }
    
    const formData = {
        wallet_address: walletAddress,
        email: email,
        twitter_handle: twitterHandle,
        referral_code: referralCode
    };
    
    console.log('Submitting form data:', formData);
    
    try {
        showMessage('Submitting...', 'info');
        
        const response = await fetch('/join-airdrop', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(formData)
        });
        
        console.log('Response status:', response.status);
        
        const result = await response.json();
        console.log('Response data:', result);
        
        if (result.success) {
            showMessage(result.message, 'success');
            
            // Store wallet address for tasks page
            localStorage.setItem('walletAddress', walletAddress);
            
            // Redirect to tasks page after 2 seconds
            setTimeout(() => {
                window.location.href = '/tasks?wallet=' + encodeURIComponent(walletAddress);
            }, 2000);
            
            // Clear form
            document.getElementById('airdropForm').reset();
        } else {
            showMessage(result.message, 'error');
        }
    } catch (error) {
        console.error('Error submitting form:', error);
        showMessage('Error connecting to server. Please check your connection and try again.', 'error');
    }
});

function showMessage(message, type) {
    const messageElement = document.getElementById('message');
    messageElement.textContent = message;
    messageElement.className = type;
    
    // Auto-hide success messages after 5 seconds
    if (type === 'success') {
        setTimeout(() => {
            messageElement.textContent = '';
            messageElement.className = '';
        }, 5000);
    }
}

// Add wallet address validation as user types
document.getElementById('wallet_address').addEventListener('input', function(e) {
    const address = e.target.value;
    if (address.length > 0 && (!address.startsWith('0x') || address.length !== 42)) {
        e.target.style.borderColor = 'red';
    } else {
        e.target.style.borderColor = '';
    }
});